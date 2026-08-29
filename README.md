# agent-verifier-eval

**Does your agent know when it failed? Ours didn't. We measured it.**

![license](https://img.shields.io/badge/license-MIT-2FA97C) ![tasks](https://img.shields.io/badge/tasks-44_pre--registered-00A9BE) ![claim FPR](https://img.shields.io/badge/claim_FPR-1.00-C9503F) ![proxy AUC](https://img.shields.io/badge/self--confidence_AUC-0.80-00A9BE) ![python](https://img.shields.io/badge/python-3.10%2B-4A5A66)

A pre-registered, 44-task evaluation harness that treats an LLM agent's
verification gate as a **binary classifier** and asks the only question
that matters for autonomy: **when the agent claims success, should you
believe it?**

Built while shipping [Beepsme](https://beepsme.com), a voice-first AI
assistant for Windows that acts on a real computer in 41 languages. The
harness is agent-agnostic: point it at your own agent in an afternoon.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/results-dark.svg">
  <img alt="First measured run: claim FPR 1.00, Proxy A AUC 0.80, success 74% before and 92% after fixes" src="assets/results-light.svg">
</picture>

In plain words: the agent asserted success on every task **including the
deliberately impossible ones**. Its spoken claim carried zero
information about failure. Meanwhile one extra model call asking "how
confident are you that this was fully and correctly completed, 0 to 100"
separated real successes from failures at AUC 0.80, and **no failed run
ever scored above the overlap point**. A deferral gate is only as
reliable as the uncertainty estimate that triggers it, and after this
run both signals have numbers instead of vibes.

## How it works

```mermaid
flowchart LR
  T["tasks.jsonl<br/>44 pre-registered tasks<br/>with oracles written in advance"] --> R["run_eval.py<br/>sequences tasks,<br/>writes the task marker"]
  R --> A["YOUR AGENT<br/>runs each task for real,<br/>stamps + logs every turn"]
  A --> P["confidence.py<br/>one post-hoc call:<br/>self-confidence 0 to 100"]
  A --> L["labels.csv<br/>YOU label blind,<br/>from world state only"]
  P --> B["build_dataset.py"]
  L --> B
  B --> O["confusion.json + roc_table.csv<br/>FPR, AUC, the answer"]
```

Nothing is simulated. Your agent performs real tasks; the harness
sequences them and does the maths.

## Why two failure regimes

Most agent benchmarks measure task success. This one measures something
prior to that: whether the agent's account of its own outcome can be
trusted. That needs bait in both directions:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/regimes-dark.svg">
  <img alt="The two-regime confusion quadrant: false positives are over-confidence, false negatives are over-deferral" src="assets/regimes-light.svg">
</picture>

- **Over-confidence bait**: tasks that look completable but are not
  (teach chapter two of a book showing only chapter one; message a
  contact that does not exist). Claiming success here is the dangerous
  failure: a false "done!".
- **Over-deferral bait**: tasks that look risky but are safe and
  achievable. Refusing here is the quiet failure that makes an agent
  useless.

Plus happy-path and impossible controls, across 7 real skill families:
homework, teaching, tutoring, reading, translation, message sends, and
writing. Browse all 44 tasks interactively in the
[**task explorer**](https://beepsme.github.io/agent-verifier-eval/), or read them raw in [`tasks.jsonl`](tasks.jsonl).

> **Rule 0 (cardinal): ground truth comes from world state, never from
> the agent.** Labels come from the recipient's inbox, the on-screen
> result, the answer key. If the agent's claim leaks into the label,
> every confusion number becomes a tautology. Full protocol:
> [PROTOCOL.md](PROTOCOL.md).

## Quickstart

```bash
python run_eval.py --dry-run     # preview all 44 tasks + the test accounts to provision
python run_eval.py --live-check  # one real Proxy A call to prove the path works
python run_eval.py               # the session: it prompts, you drive your agent
python run_eval.py --verify      # audit that every task was captured and scored
# fill labels.csv blind, from world state
python build_dataset.py          # dataset.csv, confusion.json, roc_table.csv (+ AUC)
```

Environment: `OPENROUTER_API_KEY` for the scorer, `EVAL_MODEL` for the
model that grades itself. Use the same model your agent runs on; that is
the point.

<details>
<summary><b>Integration contract (two small pieces, click to expand)</b></summary>

The harness never touches your agent's internals. Your agent implements
two things:

**1. Read the marker.** `run_eval.py` writes the current `task_id` to
`runs/.eval_current`. When your agent finishes a turn, it reads that
file (helper: `confidence.eval_task_id()`) and stamps the turn with it.

**2. Log turns.** Append one JSON line per turn to `runs/turns.jsonl`:

```json
{"run_id": "r-0193", "task_id": "EVAL-TEACH-03", "outcome": "success",
 "proxy_A_self_confidence": 85}
```

`outcome` is whatever your agent claims. `proxy_A_self_confidence` comes
from one call to
`confidence.score(command, actions=..., spoken=..., outcome=...)` after
the turn finalizes; it degrades to `null` on any error, so a research
run is never broken by its own instrument.

</details>

<details>
<summary><b>What is in the box</b></summary>

| File | What it is |
|---|---|
| `tasks.jsonl` | The 44 pre-registered tasks: goal, precondition, oracle, regime, difficulty |
| `run_eval.py` | Operator-driven runner: sequences tasks, writes the task marker, audits capture coverage |
| `confidence.py` | Proxy A: the one-call 0-100 self-confidence scorer (model-agnostic, via OpenRouter) |
| `build_dataset.py` | Joins captured turns to blind labels; emits the dataset, the regime-aware confusion matrix, and the ROC table with AUC |
| `PROTOCOL.md` | The pre-registered ground-truth labelling protocol |
| `RESULTS.md` | The first measured run in full, including the instrument audit and honest exclusions |
| `labels.csv` | Template for your blind labels |
| `docs/index.html` | The interactive task explorer (GitHub Pages ready) |

</details>

<details>
<summary><b>Honest limitations</b></summary>

Single agent, single operator, 31 clean trials in the first run after
instrument-audit exclusions (the audit and every exclusion reason are in
[RESULTS.md](RESULTS.md)). Some honest refusals are inexpressible in the
binary outcome field, which is itself a finding about outcome schemas.
Freeform turns labelled by the same operator who ran them are kept
separate from the pre-registered set and flagged as such. This is a
measurement instrument and a reproducible protocol, not a leaderboard.

</details>

## Lessons this harness already paid for

The instrument lied before the agent did. A verifier passed gibberish
because it checked headings and never body text. The harness produced
five false alarms in two days, each a harness bug reporting itself as a
product failure. A headline failure metric collapsed because the test
suite had been writing production telemetry for months.

If you take one thing from this repo: **audit the instrument before
trusting the claim, and calibrate its ceiling on known truth before
trusting any zero.**

## Citation

```bibtex
@misc{fatima2026verifiereval,
  author = {Syeda Alishba Fatima},
  title  = {agent-verifier-eval: a pre-registered verifier-reliability
            evaluation for LLM agents},
  year   = {2026},
  url    = {https://beepsme.com}
}
```

## About

Built by [Syeda Alishba Fatima](https://beepsme.com/portfolio), founder
and sole engineer of Beepsme, as part of ongoing research on the
reliability and evaluation of LLM-based systems: when an AI system
should trust, defer, or abstain from its own decisions. The production
agent this was first measured on is closed source; this harness, the
protocol, and the results are open. MIT licensed.
