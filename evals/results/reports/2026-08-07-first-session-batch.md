# J2 — first session batch

`harness-v8-session` @ `8311b5bb`, n=2 on each of two new scenarios, 2026-08-07.
Analyst: Claude Code, following `.claude/skills/j2-error-analysis`.
Bundle: `python -m evals bundle --config harness-v8-session`.

## Verdict

**Not a promotion decision.** Only one configuration ran; there is no
interleaved baseline, so the promotion rule does not apply and nothing here
promotes or rejects anything. This batch is an instrument shakedown and a first
error analysis.

| run | outcome | f2p | p2p | steps | calls | tokens_in |
| --- | --- | --- | --- | ---: | ---: | ---: |
| `duration-notes_…_r1_20260807T082837Z` | pass | 6/6 | 2/2 | 8 | 38 | 85,196 |
| `duration-notes_…_r2_20260807T083651Z` | fail (`reasoning`) | 4/6 | 2/2 | 10 | 47 | 99,154 |
| `threshold-off-by-one_…_r1_20260807T082354Z` | crash (`stopping`) | 0/4 | 3/3 | 14 | 60 | 123,774 |
| `threshold-off-by-one_…_r2_20260807T083455Z` | pass | 4/4 | 3/3 | 14 | 72 | 153,786 |

**Both scenarios are solvable by this harness, and neither is trivial** — each
passed once and failed once. That is the one thing this batch establishes with
confidence, and it is what the L0 scenario could no longer do. Do not read the
2/4 as a rate: at n=2 per scenario it carries no information, and the noise
floor is around 15 points at ten times this sample.

**Cost is 10–25× the L0 numbers** (85k–154k `tokens_in` against `v6-guarded`'s
5,756 on `retry-after-case`). Some of that is the scenarios being larger, but
most is structural: a session spends orchestrator calls between every worker,
and the orchestrator is the biggest prompt in the system. Cost per *session* is
not comparable to cost per *task*, and this report does not claim it is.

## Failure roll-call

**`duration-notes` r2 — implemented the examples, not the rule.**
`verify.txt` line 20: `parse_duration("2 hours")` → `ValueError: unknown unit in
'2 hours'`. The `UNITS` table it wrote covers `sec`/`min`/`hour` but not the
plurals. The notes' two examples were `5 min` and `30 sec`; both pass. This is
the exact failure `evaluation/criteria.md` predicted for this scenario —
"satisfying the examples rather than the requirement". It also lost the
README's spelled-out example (`test_readme_shows_a_spelled_out_example`, exit 1)
while correctly removing the now-false "one-letter" claim, so the documenter did
half its job.

**`threshold-off-by-one` r1 — wrote a new module instead of editing the one that
exists.** `journal.jsonl` step 7: `ok: wrote /alerts/formatter.py`, containing
`def format_message(value, unit=None)` with the metric name hardcoded as
`"cpu"`. The requirement was to extend `alerts/message.py::format_alert`.
Nothing imports the new module, so `test_unit_is_appended` fails with
`TypeError: format_alert() takes 2 positional arguments but 3 were given`. The
run then spent steps 9–14 exploring for the file it had itself invented (step 9:
"Located `alerts/formatter.py` defining `format_message(value, unit=None)`").

**Unexplained, and I want to flag it rather than smooth it over.** The same run
records at step 3 `ok: replaced 1 occurrence(s) in alerts/rules.py`, and step 4
verified with two successful commands — yet `alerts/rules.py` does not appear
anywhere in `diff.patch`, and `should_alert(80, 80)` still returns `True` in the
graded copy. Either the edit never landed where the tool said it did, or
something reverted it. r2 of the same scenario made the same edit and it
survived (step 10, `ok: replaced 1 occurrence(s) in alerts/message.py`, 4/4
f2p), so this is not a systematic breakage. **A journal entry claiming an edit
that the tree does not contain is the most serious thing in this batch**, because
the entire prose-review model rests on the journal being the one artefact that
cannot lie. See "what this cannot support" below.

## Taxonomy

Subdividing `reasoning`, as the standing job requires — the existing four
classes put 12 of 13 prior failures in one bucket.

| Proposed class | n | Where | What would fix it |
| --- | ---: | --- | --- |
| `write-applied-nothing` | 4 | dur-r1 step 3; thr-r2 steps 3, 6, 9 | The writer is briefed, produces prose, applies no edit. A retry with the failure quoted back, or refusing to leave `write` without an applied edit |
| `think-leakage` | 3 | thr-r1 steps 5, 6; thr-r2 step 4 | Reasoning models emit `<think>` blocks that land verbatim in the finding and consume the note budget. Strip them in `parse_report` |
| `examples-not-rule` | 1 | dur-r2 (`2 hours`) | Brief the *rule*, not the notes' examples. Anecdote at n=1, but it is the failure the criteria predicted |
| `invented-a-parallel-module` | 1 | thr-r1 (`formatter.py`) | Require the writer to name an existing file it read, or refuse `create_file` when the brief describes a change to existing behaviour |
| `docs-half-updated` | 1 | dur-r2 (claim removed, example not added) | — |

`write-applied-nothing` is the dominant waste and the cheapest to attack: **4 of
the 46 recorded steps did nothing at all**, and in `threshold-off-by-one` r2
three of them ran consecutively before step 10 finally applied the edit.

## Three defects in the instrument, all mine, all now fixed

Found while reading the evidence rather than by any test, which is the argument
for reading the evidence.

1. **`.git` survived the prune**, so every diff in this batch is mostly binary
   git objects and `diff_files` is unusable — `duration-notes` r2 reports 12
   files touched, of which 8 are `.git/objects/…`. Cause: git marks objects
   read-only and `rmtree(..., ignore_errors=True)` swallowed the failure on
   Windows. Fixed with an `onexc` handler that clears the bit.
2. **A clean "exhausted" was recorded as `crash`.** The session exited non-zero
   whenever it did not reach `done`, and the runner maps a non-zero exit to
   `crash`. So R3's "a stuck session reports rather than dies" was recorded as
   exactly the thing it is supposed to prevent. `threshold-off-by-one` r1 is
   labelled `crash`/`stopping` and it did not crash: it ran out of budget,
   wrote its rationale, and exited cleanly. Fixed — a clean end exits 0 and the
   hidden tests decide pass or fail.
3. **Every rationale reported "Files changed: 0."** The pattern lived inside an
   f-string with doubled escaping, so it matched a literal backslash. Fixed,
   with a test.

Defects 1 and 3 mean **no diff-derived metric from this batch should be quoted**.
The hidden-test ratios and the journals are unaffected.

## Recommendations

Three, each with how it would be measured.

**R1 — Refuse to leave `write` with nothing applied; retry once with the tool's
error quoted back.** Targets `write-applied-nothing`, 4 instances, the largest
category. Measure: that category's count per session, and steps-to-first-applied-
edit (currently 10 in `threshold-off-by-one` r2, 4 in `duration-notes` r1).
Expected to move both; it cannot affect correctness, so a pass-rate change would
be noise, not evidence.

**R2 — Strip `<think>` blocks and placeholder echoes in `parse_report`.**
Targets `think-leakage`, 3 instances, plus a related single instance where a
model returned my own placeholder verbatim — `threshold-off-by-one` r2 step 5
reports `<what you found or did, in under 80 words>"`. Measure: count of
findings containing `<think` or `<what you`, which should go to zero, and note
budget consumed per step. Purely mechanical; deterministic to verify.

**R3 — Have the orchestrator's brief name the file to change when one is
already known.** Targets `invented-a-parallel-module` and, indirectly,
`examples-not-rule`: both runs that failed had the right file identified by an
explorer before the writer went elsewhere (`threshold-off-by-one` r1 step 1
names `alerts/rules.py` and the message formatting; step 7 writes a new file
anyway). Measure: fraction of `create_file` calls in scenarios whose reference
solution creates no file — currently 1 of 2 failing runs, and it should be 0.

I am **not** recommending a topology change. Nothing in this batch is a
`retrieval` or `tooling` failure; the explorers found the right code in every
run. Redesigning the graph would be answering a question nobody asked.

## What this evidence cannot support

- **Any claim about pass rate.** n=2 per scenario. The two scenarios each
  passed once. That is consistent with anything from 30% to 70%.
- **The unexplained missing edit** in `threshold-off-by-one` r1. I can show the
  journal claims it and the tree lacks it; I cannot show why, because the
  workdir is deleted after the run and the diff for that batch is corrupted by
  defect 1. What would settle it: re-run that scenario with the prune fixed, and
  have the session record `git diff --stat` after each commit, so a claimed edit
  and the tree's state are recorded side by side at the same instant.
- **Whether the doc-grading tests measure documentation quality or just string
  presence.** They check that a false claim is gone and an example appears.
  `duration-notes` r1 passed both with a one-line README edit. That may be all
  the mechanism can ever check, in which case doc *quality* belongs to J1.
- **Whether sessions cost more than tasks inherently, or only on these
  scenarios.** No task-shaped configuration has run against these scenarios.

## Conflict of interest

I wrote this harness and I am analysing it. Two places that shows: the three
instrument defects above are my bugs, and finding them makes the analysis look
productive while the thing being measured barely moved. And R1–R3 are all
refinements of a design I chose — none of them questions whether the session
topology is right at all. The honest counter-hypothesis I am not well placed to
test is that the orchestrator-per-step design is simply expensive for what it
delivers, and that a fixed pipeline with a documenter bolted on would do the
same work for a third of the calls. That comparison needs a second
configuration, not a better report.

## Comments

<!-- Andrés: write here. Disagreements with the diagnosis, directions the
analysis missed, recommendations you do not want pursued. The next report reads
this section first and says where it acted on it. -->
