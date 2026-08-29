#!/usr/bin/env python
"""Build the verifier-reliability dataset from captured turns + hand labels.

Two label sources, same math, merged into one dataset (column ``source``):

  STRUCTURED  the 44 pre-registered tasks. Joins ``runs/turns.jsonl``
              to ``labels.csv`` on ``task_id``. Oracle written in
              advance -> cleanest, blind labels. This is the paper's headline.

  FREEFORM    "whatever you test", every ordinary turn is captured too. Since
              there is no pre-written oracle, you label each turn yourself after
              the fact (from world state) in ``freeform_labels.csv``,
              joined to the turns on ``run_id``. More ecologically valid, larger
              N; the cost is rigor (you label your own turns, so see the honest
              caveat in EVAL_STATUS.md §"opportunistic path").

Workflow for the freeform path:
  1. python build_dataset.py --export-unlabeled   # appends new untagged turns
  2. fill ground_truth_success + achievable in freeform_labels.csv (world state)
  3. python build_dataset.py                        # ingests both sources

Each turn carries ``task_id`` (structured) and always a ``run_id`` +
``proxy_A_self_confidence``. Outputs land in ``out/``:

  dataset.csv        task_id, ground_truth, verifier_score_or_decision, reported_outcome
  dataset_full.csv   + skill, intended_regime, achievable, proxy_A, claim, gt_success, outcome
  confusion.json     regime-aware 2x2: TP / FP(=over-confidence) / TN / FN(=over-deferral) + rates
  roc_table.csv      threshold, tpr, fpr from the continuous Proxy A score (+ AUC)

NOTHING IS SIMULATED. Every value is read from real logs / your labels. Rows
whose task_id is absent from labels.csv, or whose label is blank, are skipped
and counted. When a task has several turns, the LAST (final attempt) is used.

The confusion matrix uses BOTH labels, per the two-regime taxonomy:
  predicted positive  = the agent CLAIMED success (turn outcome == "success")
  - claimed  & gt_success==1            -> TP
  - claimed  & gt_success==0            -> FP   (over-confidence)
  - not-claimed & achievable==0         -> TN   (correct deferral)
  - not-claimed & achievable==1         -> FN   (over-deferral)
  - not-claimed & achievable==unknown   -> excluded (cannot place; counted)

The ROC sweeps the continuous Proxy A score against realized success
(y_true = gt_success), answering "does self-confidence discriminate runs that
actually succeeded from runs that did not?"
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

_EVAL_DIR = Path(__file__).resolve().parent
_OUT_DIR = _EVAL_DIR / "out"


def _runs_dir() -> Path:
    override = os.getenv("RESEARCH_DIR", "").strip()
    return Path(override) if override else _EVAL_DIR / "runs"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_tasks(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            t = json.loads(line)
            out[t["task_id"]] = t
    return out


def load_last_turns(path: Path) -> dict[str, dict]:
    """task_id -> the LAST turn record carrying that id (by file order)."""
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        tid = (rec.get("task_id") or "").strip()
        if tid:
            out[tid] = rec  # later lines overwrite -> last wins
    return out


def audit_coverage(task_ids: list[str], turns: dict[str, dict]) -> dict:
    """Per-task capture status: did each manifest task land a turn, with a score?

    Used by run_eval.py --verify to confirm a live session captured cleanly
    BEFORE time is spent labelling. Pure / no I/O so it is unit-testable.
    """
    rows = []
    for tid in task_ids:
        t = turns.get(tid)
        rows.append({
            "task_id": tid,
            "captured": t is not None,
            "has_proxy_A": bool(t and t.get("proxy_A_self_confidence") is not None),
            "outcome": (t or {}).get("outcome", ""),
        })
    return {
        "rows": rows,
        "n_tasks": len(task_ids),
        "n_captured": sum(1 for r in rows if r["captured"]),
        "n_scored": sum(1 for r in rows if r["has_proxy_A"]),
        "missing": [r["task_id"] for r in rows if not r["captured"]],
        "captured_no_score": [r["task_id"] for r in rows if r["captured"] and not r["has_proxy_A"]],
    }


def _parse_label_int(val: str) -> int | None:
    v = (val or "").strip().lower()
    if v in ("0", "1"):
        return int(v)
    return None  # blank / "unknown" / anything else


def load_labels(path: Path) -> dict[str, dict]:
    """task_id -> {gt_success: 0/1 or None, achievable: 0/1 or None}."""
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            tid = (row.get("task_id") or "").strip()
            if not tid or tid.startswith("EXAMPLE"):
                continue
            out[tid] = {
                "gt_success": _parse_label_int(row.get("ground_truth_success", "")),
                "achievable": _parse_label_int(row.get("achievable", "")),
            }
    return out


# ---------------------------------------------------------------------------
# Freeform (opportunistic) path, label your real testing turns, joined on run_id
# ---------------------------------------------------------------------------

def load_turns_by_run_id(path: Path) -> dict[str, dict]:
    """run_id -> turn record (EVERY turn, tagged or not). Keyed by the unique
    per-turn run_id so freeform turns (no task_id) are still joinable."""
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = (rec.get("run_id") or "").strip()
        if rid:
            out[rid] = rec
    return out


def load_freeform_labels(path: Path) -> dict[str, dict]:
    """run_id -> {gt_success, achievable, regime, skill} from freeform_labels.csv."""
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            rid = (row.get("run_id") or "").strip()
            if not rid or rid.startswith("EXAMPLE"):
                continue
            out[rid] = {
                "gt_success": _parse_label_int(row.get("ground_truth_success", "")),
                "achievable": _parse_label_int(row.get("achievable", "")),
                "regime": (row.get("regime") or "").strip() or "freeform",
                "skill": (row.get("skill") or "").strip(),
            }
    return out


def build_freeform_rows(turns_by_run: dict, ff_labels: dict) -> tuple[list[dict], dict]:
    """Rows for opportunistically-labelled freeform turns, joined on run_id.

    Same schema + downstream math as structured tasks; task_id is the run_id so
    the row stays identifiable, source='freeform' so it can be split out later.
    """
    rows: list[dict] = []
    skipped = {"no_turn": [], "no_gt_label": []}
    for rid, lab in ff_labels.items():
        gt = lab["gt_success"]
        if gt is None:
            skipped["no_gt_label"].append(rid)
            continue
        turn = turns_by_run.get(rid)
        if turn is None:
            skipped["no_turn"].append(rid)
            continue
        claim = 1 if turn.get("outcome") == "success" else 0
        proxy_a = turn.get("proxy_A_self_confidence")
        proxy_a = int(proxy_a) if isinstance(proxy_a, (int, float)) else None
        logged_skills = turn.get("skills") or []
        skill = lab["skill"] or (logged_skills[0] if logged_skills else "")
        rows.append({
            "task_id": rid,
            "skill": skill,
            "intended_regime": lab["regime"],
            "ground_truth_success": gt,
            "achievable": lab["achievable"],
            "claim": claim,
            "proxy_A": proxy_a,
            "outcome": turn.get("outcome", ""),
            "verifier_score_or_decision": proxy_a if proxy_a is not None else claim,
            "source": "freeform",
        })
    return rows, skipped


def export_unlabeled(turns_path: Path, ff_path: Path) -> int:
    """Append freeform (untagged) turns not yet in freeform_labels.csv, with
    blank truth columns to fill in. Idempotent: only new run_ids are added.

    The sheet deliberately omits the agent's outcome + Proxy A so you can label
    the truth without being anchored by the prediction (Rule 0, as far as is
    possible for turns you ran yourself)."""
    turns = load_turns_by_run_id(turns_path)
    existing: set[str] = set()
    if ff_path.exists():
        with ff_path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                rid = (row.get("run_id") or "").strip()
                if rid:
                    existing.add(rid)
    header = ["run_id", "ts_utc", "command", "skill",
              "ground_truth_success", "achievable", "regime", "notes"]
    new_rows: list[dict] = []
    for rid, rec in turns.items():
        if rid in existing:
            continue
        if (rec.get("task_id") or "").strip():
            continue  # tagged -> handled by the structured path, not freeform
        cmd = (rec.get("command") or "").strip()
        if not cmd:
            continue
        logged = rec.get("skills") or []
        new_rows.append({
            "run_id": rid,
            "ts_utc": rec.get("ts_utc", ""),
            "command": cmd[:200],
            "skill": (logged[0] if logged else ""),
            "ground_truth_success": "", "achievable": "", "regime": "", "notes": "",
        })
    is_new_file = not ff_path.exists()
    with ff_path.open("a", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        if is_new_file:
            w.writeheader()
        for r in new_rows:
            w.writerow(r)
    print(f"Exported {len(new_rows)} new freeform turn(s) -> {ff_path}")
    if new_rows:
        print("  Fill ground_truth_success (1/0) + achievable (1/0/blank) from WORLD STATE")
        print("  (what actually happened), NOT from what the agent reported. Optional: skill,")
        print("  regime. Then: python research/eval/build_dataset.py")
    else:
        print("  Nothing new to label (all captured freeform turns already in the sheet).")
    return 0


# ---------------------------------------------------------------------------
# Join + dataset rows
# ---------------------------------------------------------------------------

def build_rows(tasks: dict, turns: dict, labels: dict) -> tuple[list[dict], dict]:
    """Return (rows, skipped_counts). One row per labelled task that also ran."""
    rows: list[dict] = []
    skipped = {"no_turn": [], "no_gt_label": []}
    for tid, lab in labels.items():
        gt = lab["gt_success"]
        if gt is None:
            skipped["no_gt_label"].append(tid)
            continue
        turn = turns.get(tid)
        if turn is None:
            skipped["no_turn"].append(tid)
            continue
        claim = 1 if turn.get("outcome") == "success" else 0
        proxy_a = turn.get("proxy_A_self_confidence")
        proxy_a = int(proxy_a) if isinstance(proxy_a, (int, float)) else None
        task = tasks.get(tid, {})
        rows.append({
            "task_id": tid,
            "skill": task.get("skill", ""),
            "intended_regime": task.get("intended_regime", ""),
            "ground_truth_success": gt,
            "achievable": lab["achievable"],
            "claim": claim,
            "proxy_A": proxy_a,
            "outcome": turn.get("outcome", ""),
            # user-spec single column: continuous score if present, else the
            # binary operating-point decision.
            "verifier_score_or_decision": proxy_a if proxy_a is not None else claim,
            "source": "structured",
        })
    return rows, skipped


def confusion(rows: list[dict]) -> dict:
    """Regime-aware 2x2 using claim (prediction) + gt_success/achievable (truth)."""
    tp = fp = tn = fn = 0
    excluded_unknown_achievable = 0
    for r in rows:
        if r["claim"] == 1:
            if r["ground_truth_success"] == 1:
                tp += 1
            else:
                fp += 1  # over-confidence
        else:  # claim == 0
            ach = r["achievable"]
            if ach == 1:
                fn += 1  # over-deferral
            elif ach == 0:
                tn += 1
            else:
                excluded_unknown_achievable += 1

    def _safe(n: int, d: int) -> float | None:
        return round(n / d, 4) if d else None

    return {
        "TP": tp, "FP_over_confidence": fp, "TN": tn, "FN_over_deferral": fn,
        "excluded_unknown_achievable": excluded_unknown_achievable,
        "n_scored": tp + fp + tn + fn,
        "precision": _safe(tp, tp + fp),
        "recall_TPR": _safe(tp, tp + fn),
        "FPR": _safe(fp, fp + tn),
        "over_confidence_rate": _safe(fp, tp + fp),   # of claimed-success, share that failed
        "over_deferral_rate": _safe(fn, tn + fn),     # of not-claimed, share that was achievable
    }


# ---------------------------------------------------------------------------
# ROC over the continuous Proxy A score
# ---------------------------------------------------------------------------

def _manual_roc(y_true: list[int], y_score: list[float]) -> tuple[list[float], list[float], list[float]]:
    pairs = list(zip(y_score, y_true, strict=True))
    P = sum(1 for y in y_true if y == 1)
    N = sum(1 for y in y_true if y == 0)
    thresholds = sorted(set(y_score), reverse=True)
    fpr_l, tpr_l, thr_l = [], [], []
    for thr in [float("inf"), *thresholds]:
        tp = sum(1 for s, y in pairs if s >= thr and y == 1)
        fp = sum(1 for s, y in pairs if s >= thr and y == 0)
        tpr_l.append(tp / P if P else 0.0)
        fpr_l.append(fp / N if N else 0.0)
        thr_l.append(thr)
    return fpr_l, tpr_l, thr_l


def _trapezoid_auc(fpr: list[float], tpr: list[float]) -> float:
    order = sorted(range(len(fpr)), key=lambda i: fpr[i])
    f = [fpr[i] for i in order]
    t = [tpr[i] for i in order]
    auc = 0.0
    for i in range(1, len(f)):
        auc += (f[i] - f[i - 1]) * (t[i] + t[i - 1]) / 2.0
    return round(auc, 4)


def roc(rows: list[dict]) -> dict:
    """ROC of Proxy A vs realized success. Returns table + AUC, or a reason it
    could not be computed (never fabricates a curve)."""
    scored = [r for r in rows if r["proxy_A"] is not None]
    y_true = [r["ground_truth_success"] for r in scored]
    y_score = [float(r["proxy_A"]) for r in scored]
    if len(scored) < 2:
        return {"ok": False, "reason": f"only {len(scored)} rows have a Proxy A score"}
    if len(set(y_true)) < 2:
        return {"ok": False, "reason": "ground truth has only one class among scored rows; ROC undefined"}

    try:
        from sklearn.metrics import roc_auc_score, roc_curve
        fpr, tpr, thr = roc_curve(y_true, y_score)
        auc = round(float(roc_auc_score(y_true, y_score)), 4)
        fpr, tpr, thr = list(fpr), list(tpr), list(thr)
        backend = "sklearn"
    except Exception:
        fpr, tpr, thr = _manual_roc(y_true, y_score)
        auc = _trapezoid_auc(fpr, tpr)
        backend = "manual"

    table = [
        {"threshold": ("inf" if t == float("inf") else round(float(t), 4)),
         "tpr": round(float(tp), 4), "fpr": round(float(fp), 4)}
        for t, tp, fp in zip(thr, tpr, fpr, strict=True)
    ]
    return {"ok": True, "backend": backend, "auc": auc, "n_scored": len(scored), "table": table}


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fieldnames})


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Build the verifier-reliability dataset from captured turns + labels.")
    ap.add_argument("--export-unlabeled", action="store_true",
                    help="append freeform (untagged) turns to freeform_labels.csv to hand-label, then exit")
    args = ap.parse_args(argv)

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    turns_path = _runs_dir() / "turns.jsonl"
    ff_path = _EVAL_DIR / "freeform_labels.csv"

    if args.export_unlabeled:
        return export_unlabeled(turns_path, ff_path)

    tasks = load_tasks(_EVAL_DIR / "tasks.jsonl")
    turns = load_last_turns(turns_path)
    turns_by_run = load_turns_by_run_id(turns_path)
    labels = load_labels(_EVAL_DIR / "labels.csv")
    ff_labels = load_freeform_labels(ff_path)

    if not labels and not ff_labels:
        print("No labels found. Pick a path:")
        print("  structured: cp labels_template.csv labels.csv ; fill it (blind)")
        print("  freeform:   python build_dataset.py --export-unlabeled ; fill freeform_labels.csv")
        return 1

    rows, skipped = build_rows(tasks, turns, labels)
    ff_rows, ff_skipped = build_freeform_rows(turns_by_run, ff_labels)
    all_rows = rows + ff_rows
    if not all_rows:
        print("No labelled rows with a matching captured turn. Nothing to build.")
        print(f"  structured labels: {len(labels)}  freeform labels: {len(ff_labels)}")
        for tag, sk in (("structured", skipped), ("freeform", ff_skipped)):
            if sk["no_turn"]:
                print(f"  {tag} labelled but never captured: {', '.join(sk['no_turn'])}")
        return 1

    # 1) user-spec 4-column dataset
    _write_csv(
        _OUT_DIR / "dataset.csv",
        ["task_id", "ground_truth", "verifier_score_or_decision", "reported_outcome"],
        [{"task_id": r["task_id"], "ground_truth": r["ground_truth_success"],
          "verifier_score_or_decision": r["verifier_score_or_decision"],
          "reported_outcome": r["outcome"]} for r in all_rows],
    )
    # 2) richer dataset (with source so structured/freeform can be split later)
    _write_csv(
        _OUT_DIR / "dataset_full.csv",
        ["task_id", "source", "skill", "intended_regime", "ground_truth_success",
         "achievable", "claim", "proxy_A", "outcome", "verifier_score_or_decision"],
        all_rows,
    )
    # 3) confusion (combined)
    conf = confusion(all_rows)
    (_OUT_DIR / "confusion.json").write_text(json.dumps(conf, indent=2), encoding="utf-8")
    # 4) roc (combined)
    roc_res = roc(all_rows)
    if roc_res["ok"]:
        _write_csv(_OUT_DIR / "roc_table.csv", ["threshold", "tpr", "fpr"], roc_res["table"])
    else:
        (_OUT_DIR / "roc_table.csv").write_text(
            f"# ROC not computed: {roc_res['reason']}\nthreshold,tpr,fpr\n", encoding="utf-8")

    # console summary
    n_struct = len(rows)
    n_free = len(ff_rows)
    print(f"Built dataset: {len(all_rows)} labelled+captured rows "
          f"({n_struct} structured + {n_free} freeform)  ->  {_OUT_DIR}")
    for tag, sk in (("structured", skipped), ("freeform", ff_skipped)):
        if sk["no_turn"]:
            print(f"  WARNING {tag} labelled but no captured turn ({len(sk['no_turn'])}): {', '.join(sk['no_turn'])}")
        if sk["no_gt_label"]:
            print(f"  {tag} skipped blank ground_truth ({len(sk['no_gt_label'])})")
    print("\nConfusion (operating point = agent's success claim):")
    print(f"  TP={conf['TP']}  FP/over-confidence={conf['FP_over_confidence']}  "
          f"TN={conf['TN']}  FN/over-deferral={conf['FN_over_deferral']}")
    print(f"  precision={conf['precision']}  TPR={conf['recall_TPR']}  FPR={conf['FPR']}")
    if conf["excluded_unknown_achievable"]:
        print(f"  ({conf['excluded_unknown_achievable']} not-claimed rows excluded: achievable=unknown)")
    if roc_res["ok"]:
        print(f"\nROC (Proxy A vs realized success): AUC={roc_res['auc']} "
              f"[{roc_res['backend']}, n={roc_res['n_scored']}], table -> roc_table.csv")
    else:
        print(f"\nROC not computed: {roc_res['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
