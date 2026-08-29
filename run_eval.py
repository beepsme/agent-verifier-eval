#!/usr/bin/env python
"""Semi-manual eval runner for the verifier-reliability study.

Walks ``research/eval/tasks.jsonl`` one task at a time. For each task it writes
a cross-process marker (``.eval_current``) that your LIVE agent reads, so
every turn you perform while that task is active is stamped with its ``task_id``
and a Proxy A self-confidence score in ``runs/turns.jsonl``.

The sends are REAL, so this is operator-driven: it prints the command to issue
and the success oracle, then waits for you to perform it against your agent and press
Enter. It captures no outcomes itself, your agent does the logging; this only
sequences the tasks and tells the app which task each turn belongs to.

PREREQUISITES
  1. your agent stamps each turn with the marker task_id (see README, Integration).
  2. Run your agent from a FROZEN build (tag it; the paper run used one tag).
  3. Provision the EVAL_* / WA_EVAL_* test accounts named in tasks.jsonl.

USAGE
  python run_eval.py --live-check        # confirm capture works
  python run_eval.py                      # run all 44 tasks
  python run_eval.py --skill tutor        # one family (e.g. tutor)
  python run_eval.py --ids EVAL-DSK-01,EVAL-DSK-02
  python run_eval.py --start-at EVAL-HW-03  # resume mid-run
  python run_eval.py --verify             # audit coverage after
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import confidence as research_confidence

_EVAL_DIR = Path(__file__).resolve().parent
_TASKS = _EVAL_DIR / "tasks.jsonl"


def _load_tasks() -> list[dict]:
    return [json.loads(line) for line in _TASKS.read_text(encoding="utf-8").splitlines() if line.strip()]


def _set_marker(task_id: str) -> None:
    p = research_confidence._marker_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(task_id, encoding="utf-8")


def _clear_marker() -> None:
    try:
        research_confidence._marker_path().write_text("", encoding="utf-8")
    except Exception:
        pass


def _live_check() -> int:
    """Do ONE real Proxy A scoring call to prove the capture path end-to-end.

    This is the cheapest insurance against burning a 40-task session only to
    find proxy_A is null. It scores a trivial 'completed' task; a number back
    means key + OpenRouter + parsing all work.
    """
    os.environ["RESEARCH_LOG"] = "1"  # this process only
    print("Live-check: issuing one real Proxy A scoring call...")
    s = research_confidence.score(
        "say hello to the user", actions=["chat:hello"], spoken="Hello!", outcome="success",
    )
    if s is None:
        print("FAILED: Proxy A returned None.")
        print("  Check: OPENROUTER_API_KEY is set, network reachable, model id valid.")
        return 1
    print(f"OK: Proxy A path works (returned {s}/100).")
    print("  Now make sure your agent logs turns to runs/turns.jsonl and start it.")
    return 0


def _verify(tasks: list[dict]) -> int:
    """Audit how many of the (filtered) tasks were captured + scored."""
    import build_dataset as bd  # same directory, on sys.path[0]
    turns = bd.load_last_turns(bd._runs_dir() / "turns.jsonl")
    rep = bd.audit_coverage([t["task_id"] for t in tasks], turns)
    print(f"Capture coverage: {rep['n_captured']}/{rep['n_tasks']} captured, "
          f"{rep['n_scored']} with a Proxy A score.\n")
    for r in rep["rows"]:
        flag = "ok      " if r["has_proxy_A"] else ("no-score" if r["captured"] else "MISSING ")
        print(f"  [{flag}] {r['task_id']:<22} outcome={r['outcome'] or '-'}")
    if rep["missing"]:
        print(f"\nNot captured ({len(rep['missing'])}). Re-run:")
        print(f"  python run_eval.py --ids {','.join(rep['missing'])}")
    if rep["captured_no_score"]:
        print(f"\nCaptured but proxy_A is null ({len(rep['captured_no_score'])}): "
              f"{','.join(rep['captured_no_score'])}")
        print("  -> your agent likely was not logging or lacked OPENROUTER_API_KEY.")
        print("     Fix it (run --live-check to confirm), then re-run those ids.")
    if not rep["missing"] and not rep["captured_no_score"] and rep["n_tasks"]:
        print("\nAll selected tasks captured AND scored. Ready to label labels.csv.")
    return 0


def _dry_run(tasks: list[dict]) -> int:
    """Preview the session: print every selected task's command + oracle, and
    the union of test-account tokens to provision. No marker, no app needed."""
    print(f"DRY RUN, {len(tasks)} task(s). No marker written, no app needed.\n")
    for i, t in enumerate(tasks, 1):
        print(f"[{i}/{len(tasks)}] {t['task_id']}  ({t['skill']} / {t['intended_regime']} / {t['difficulty']})")
        if t.get("precondition"):
            print(f"  setup   : {t['precondition']}")
        print(f"  command : {t['goal']}")
        print(f"  oracle  : {t['oracle']}")
        if t.get("env_tokens"):
            print(f"  env     : {', '.join(t['env_tokens'])}")
        print()
    toks = sorted({tok for t in tasks for tok in t.get("env_tokens", [])})
    if toks:
        print(f"Test-account tokens to provision ({len(toks)}): {', '.join(toks)}")
    return 0


def _preflight() -> None:
    """Non-fatal reminders before a session (this process cannot see your agent's env)."""
    print("Preflight reminders:")
    print("  - Start your agent from a FROZEN build (record the tag).")
    print("  - Your agent must be logging turns to runs/turns.jsonl (see README).")
    if not os.getenv("OPENROUTER_API_KEY"):
        print("  ! OPENROUTER_API_KEY not set, proxy_A will be null.")
    print("  Tip: run with --live-check first to confirm the Proxy A path works.\n")


def _filter(tasks: list[dict], args: argparse.Namespace) -> list[dict]:
    if args.skill:
        tasks = [t for t in tasks if t["skill"] == args.skill]
    if args.ids:
        wanted = {s.strip() for s in args.ids.split(",") if s.strip()}
        tasks = [t for t in tasks if t["task_id"] in wanted]
    if args.start_at:
        ids = [t["task_id"] for t in tasks]
        if args.start_at in ids:
            tasks = tasks[ids.index(args.start_at):]
    return tasks


def main() -> int:
    ap = argparse.ArgumentParser(description="Semi-manual verifier-reliability eval runner.")
    ap.add_argument("--skill", help="only this skill family (e.g. homework, gmail_send)")
    ap.add_argument("--ids", help="comma-separated task_ids")
    ap.add_argument("--start-at", help="skip tasks until this task_id (resume)")
    ap.add_argument("--verify", action="store_true",
                    help="audit capture coverage from turns.jsonl and exit (no tasks run)")
    ap.add_argument("--live-check", action="store_true",
                    help="do ONE real Proxy A scoring call to confirm the path, then exit")
    ap.add_argument("--dry-run", action="store_true",
                    help="preview every selected task's command + oracle; no marker, no waiting, no app")
    args = ap.parse_args()

    if args.live_check:
        return _live_check()

    tasks = _filter(_load_tasks(), args)
    if not tasks:
        print("No tasks matched the filter.")
        return 1

    if args.dry_run:
        return _dry_run(tasks)

    if args.verify:
        return _verify(tasks)

    print("=" * 72)
    print("Verifier-reliability eval runner")
    print(f"  tasks to run : {len(tasks)}")
    print("  controls     : [Enter]=done & next   s=skip   q=quit")
    print("=" * 72)
    _preflight()

    attempted: list[str] = []
    try:
        for i, t in enumerate(tasks, 1):
            tid = t["task_id"]
            _set_marker(tid)
            print(f"\n[{i}/{len(tasks)}] {tid}   ({t['skill']} / {t['intended_regime']} / {t['difficulty']})")
            if t.get("precondition"):
                print(f"  SET UP FIRST: {t['precondition']}")
            print(f"  SAY/TYPE to your agent:\n    > {t['goal']}")
            print(f"  ORACLE (how you'll label ground truth later):\n    {t['oracle']}")
            if t.get("env_tokens"):
                print(f"  env tokens needed: {', '.join(t['env_tokens'])}")
            choice = input("  [Enter]=done  s=skip  q=quit > ").strip().lower()
            if choice == "q":
                print("Quitting.")
                break
            if choice == "s":
                print("  skipped.")
                continue
            attempted.append(tid)
            print("  recorded as attempted.")
    finally:
        _clear_marker()

    out_dir = _EVAL_DIR / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    note = out_dir / "run_log.csv"
    new = not note.exists()
    with note.open("a", encoding="utf-8") as fh:
        if new:
            fh.write("attempted_at_iso,task_id\n")
        stamp = datetime.now().isoformat(timespec="seconds")
        for tid in attempted:
            fh.write(f"{stamp},{tid}\n")

    print(f"\nDone. Attempted {len(attempted)} task(s). Marker cleared.")
    print("Next: python run_eval.py --verify   (confirm capture before labelling)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
