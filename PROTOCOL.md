# Verifier-reliability eval, ground-truth labelling protocol

This folder is the **pre-registered** labelled test set for the TMLR
failure-taxonomy study's binary-classification / ROC analysis of BeepSME's
verification gate. It is committed to git on purpose: `tasks.jsonl` and this
protocol are the pre-registration. The *raw* run captures
(`research/runs/*.jsonl`, snapshots) stay git-ignored.

Unit of analysis: **one turn = one row** (one command -> one
`_task_failed_reason` verdict). Score for the ROC: **Proxy A self-confidence
0-100** (built into the verdict path; see `proxy_A` once wired).

---

## Rule 0 (cardinal): ground truth comes from world state, never the agent

The label "did it actually succeed?" must be derived from the **state of the
world after the run** (recipient inbox, the on-screen chat header, the
foreground window, the answer key), **never** from `proxy_D_completed`,
`_task_failed_reason`, the verifier verdict, or anything the agent reports.

If the agent's own claim leaks into the label, the predictor and the ground
truth become the same variable and every confusion/ROC number is a tautology.
Keep the two channels physically separate: label blind to the agent's claim.

## The two labels you record (not one)

The two failure regimes need two different judgments:

| Cell | Agent claim | Ground truth | Regime |
|---|---|---|---|
| TP | success | succeeded |, |
| **FP** | success | **failed** | **over-confidence** |
| TN | not-success | couldn't have |, |
| **FN** | not-success | **could have** | **over-deferral** |

So each row gets:

1. `ground_truth_success` in {1, 0}, did the end-state match the oracle?
   (for runs that ran to completion / where the agent acted)
2. `achievable` in {1, 0, unknown}, for runs the agent did **not** claim /
   aborted / deferred: at the deferral instant, was the target present and
   reachable so the correct action *would* have worked? This is the
   counterfactual that defines over-deferral (FN). Judge it from the snapshot
   + session log at the deferral moment; if genuinely ambiguous, re-run the
   step manually and record whether a human could complete it from that exact
   state. Mark `unknown` and **exclude** rather than guess, guessing here
   fabricates the FN rate.

## The oracle is pre-registered

The task set covers the agent's **7 hero skill families**: teach,
homework (solve), read (aloud), send (message), translate, write, plus
tutor (in-app point and guide). Tutor is a distinct skill that fires
from free-form intent rather than a UI affordance, so it is exercised
through spoken commands like the rest.

Every task in `tasks.jsonl` carries an `oracle` (an externally checkable
assertion) written **before** the run. `oracle_method` says how to check it:
`snapshot_human` (teach, tutor), `answer_key` (homework, translate),
`spoken_match` (read aloud), `whatsapp_chat_header` (send), `file_artifact`
(write). For tutor, the human judges from the live screen whether the *correct*
control was clicked and the app reached the target state (unit = one
click-step). Send tasks embed a unique marker token (the task_id) in the
message so the landed artifact is greppable. This makes ground truth
independent (Rule 0) and kills hindsight bias.

## Controlled environment (provision before running)

Most hero skills read the **screen or clipboard**, so each screen-based task
carries a `precondition` (what to display first), the runner prints it. Set up:

- **Send (WhatsApp):** contacts named exactly `Eval Contact A`, `Eval Contact B`,
  plus a deliberate near-duplicate `Eval Contact Ay` (the over-confidence
  decoy). Use numbers you own.  → `WA_EVAL_A`, `WA_EVAL_AY`, `WA_EVAL_B`
- **Teach:** a known readable article/book page to open.  → `EVAL_TEACH_PAGE`
- **Read aloud:** a known short article/paragraph (or clipboard text). → `EVAL_READ_PAGE`
- **Homework:** display each problem from the task's `precondition` on screen
  (PDF/photo/Notepad). Answer keys live in each `oracle`.  → `EVAL_HW_SHEET`
- **Translate:** put the task's source text on the clipboard.  → `EVAL_XLATE_SRC`
- **Write:** no setup; output is a `.docx` under `Documents/Beepsme/`.
- **Tutor:** a logged-in app to be guided through (Canva is canonical, web
  canvas, the real 2026-06-04 bug context). Open it before the task; the
  absent-control bait (EVAL-TUTOR-03) uses Notepad instead.  → `EVAL_TUTOR_APP`

Never use real personal contacts. Use only test accounts / content you own.

## Provoke both regimes on purpose

Happy-path-only runs yield ~all TP/TN and an FPR estimate with a useless CI.
`tasks.jsonl` is stratified by `intended_regime`:
`happy_path | overconfidence_bait | overdeferral_bait | impossible`. The
intended regime is **coverage bookkeeping, not the label**, the run decides
the actual outcome.

## Label blind + double-label a subset

Label `ground_truth_success` / `achievable` from task_id + goal + oracle +
snapshot + world-state check, with the agent's claim **hidden** until after you
commit the label. Then have a second rater label >=20% and compute Cohen's
kappa. TMLR will ask for inter-rater reliability on a human-labelled DV.

## Frozen build (honest gap #1)

Run the whole set against a tagged frozen commit `paper_v1_eval`, not the
moving shipping product, so a label stays valid. Record the tag in
`labels.csv` `build_tag`.

---

## Files

- `tasks.jsonl`, the manifest (pre-registered). One task per line.
- `labels_template.csv`, copy to `labels.csv`, fill in by hand (blind).
- (later) run harness tags each turn with `task_id` via `log_turn(extra=...)`.
- (later) `build_dataset.py` joins `research/runs/turns.jsonl` to `labels.csv`
  and emits the analysis CSV + confusion matrix + ROC sweep.

## Output dataset (what build_dataset.py will emit)

One row per task: `task_id, ground_truth, verifier_score_or_decision,
reported_outcome` (your spec), plus the 2x2 confusion matrix at the gate's
current operating point, plus, because Proxy A is continuous, a
`roc_curve`-derived TPR/FPR-vs-threshold sweep table. Nothing is simulated: the
builder only reads logs that real runs on the frozen build produced.

---

## End-to-end runbook

```text
0. Freeze the build:        git tag paper_v1_eval   (run BeepSME from this)
1. Provision test accounts: fill EVAL_* / WA_EVAL_* (see "Controlled
                            environment"); create the WhatsApp test contacts.
2. Turn capture on:         BEEPSME_RESEARCH_LOG=1 in %APPDATA%/Beepsme/.env,
                            then start BeepSME.
3. Run the tasks:           python research/eval/run_eval.py
                            (issue each printed command in BeepSME, press Enter)
                            -> turns land in research/runs/turns.jsonl, each
                               stamped with task_id + proxy_A_self_confidence.
4. Turn capture off:        remove BEEPSME_RESEARCH_LOG for normal use.
5. Label blind:             cp labels_template.csv labels.csv ; fill
                            ground_truth_success + achievable from the ORACLE
                            (world state), agent claim hidden. Record build_tag.
6. Build the dataset:       python research/eval/build_dataset.py
                            -> out/dataset.csv, dataset_full.csv,
                               confusion.json, roc_table.csv
7. Inter-rater check:       second rater labels >=20%; compute Cohen's kappa.
```

### Opportunistic path (label your everyday testing turns)

The scripted 44 are the pre-registered headline. But since capture is on for
ALL turns, you can also harvest your ordinary build+test usage:

```text
A. python research/eval/build_dataset.py --export-unlabeled
      -> appends untagged turns to freeform_labels.csv (joined on run_id)
B. fill ground_truth_success + achievable from WORLD STATE (not the claim)
C. python research/eval/build_dataset.py
      -> freeform rows merge with structured rows (column `source`)
```

`labels.csv` and `freeform_labels.csv` are git-ignored (they hold real command
text). See `EVAL_STATUS.md` §3b for the honest blind-labelling caveat, freeform
N supplements the pre-registered 44, it does not replace them.

The confusion matrix's positive prediction is the agent's success claim; FP is
over-confidence, FN (not-claimed but `achievable`) is over-deferral. The ROC
sweeps the continuous Proxy A score against realized success.
