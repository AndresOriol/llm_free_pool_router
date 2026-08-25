---
name: j2-error-analysis
description: Run a J2 batch analysis over recorded eval runs - open-code every failure, group them into a failure taxonomy with counts, and write a report that cites its evidence and ranks what to change next. Use when eval runs have been recorded and you need to know what to fix, when asked to "run J2", "analyse the batch", "do the error analysis", or after any batch of runs completes.
---

# J2 — batch error analysis

You are the **analyst**, not the grader. Your output is not a score. It is
*insight*: what went wrong, how often, and what to do about it — written so the
human can check you by opening the evidence you cite.

This is the expensive paid call in the loop and the only instrument stronger
than the system it studies, so spend it on judgement and not on file-wrangling.

## 1. Get the evidence

```bash
python -m evals bundle --out /tmp/j2-bundle.md
```

Options: `--config NAME` (repeatable) to scope to one configuration, `--since
20260806T000000Z` to scope to a batch. Read the bundle. It is ordered
failures-first and every section is labelled with its `run_id`.

Open individual files under `evals/results/runs/<run_id>/` when the bundle's
clip hides something you need — `trace.jsonl` for what the model actually did,
`journal.jsonl` and `rationale.md` for a session's own account of itself, and
`steps/NN-<role>.md` for what a role was handed and what it replied.

**Read `evals/results/reviews/<run_id>.md` first, where one exists.** Those are
per-run post-mortems written by the `trace-reviewer` subagent: the trajectory,
the turn a run could not recover from, and what context that turn had. They are
the open-coding notes for the runs they cover, done properly and against the
full evidence rather than the bundle's clip.

A review is a claim, not a fact. Where you disagree with one, say so and cite
what it missed — and if a run in this batch has no review, spawn
`trace-reviewer` for it rather than substituting the bundle's clip.

## 2. Open-code every failure

One note per failed run, in your own words, before any categorising. Say what
went wrong *specifically* — not "reasoning error" but "changed the header string
instead of making the lookup case-insensitive". Where a review exists, its
"Where it turned" section is that note; your job is to check it against the
evidence and carry it forward, not to redo it.

Read **every** failure, not a sample. Industry practice samples ~100 traces out
of thousands; here runs are scarce and expensive, so the ratio inverts and the
binding constraint is having enough varied failures, not choosing among them.

While reading, check three things that only show up in the evidence:

- **Does the rationale match the diff?** A session claiming a change the diff
  does not contain is the single most important thing you can find, because the
  whole review model assumes it does not happen.
- **Does every claim in the rationale trace to a command in the journal?**
- **Did the docs get updated?** A code change with a stale doc is a failed
  session, not a passing one with a nit.

## 3. Group them (axial coding)

Cluster the notes into named categories and count them. Rules:

- A category needs **at least two instances**. One occurrence is an anecdote;
  name it in the roll-call and leave it uncategorised.
- Prefer **splitting an existing class over inventing a parallel one**. The
  current taxonomy (`retrieval` / `tooling` / `reasoning` / `stopping`, in
  `docs/10-metrics.md`) put 12 of 13 failures in `reasoning`, which is a rename
  of "failed" rather than a diagnosis. Subdividing it is the standing job.
- Each category must name **what would fix it**. A category whose fix is "be
  smarter" is not a category.

## 4. Compute the verdict mechanically

Promote / draw / reject comes from the promotion rule in
`docs/08-evaluation-method.md#87-the-promotion-rule`, not from your reading.
State it before any narrative so the narrative cannot colour it.

**Pass rates at these sample sizes are noise.** The noise floor is around 15
points at ~30 trials; one configuration in this repo scored 3/3 and 1/3 on
consecutive batches of the same scenario. Cost metrics (`provider_calls`,
`tokens_in`) replicate; pass rates do not. Say so rather than ranking on them.

## 5. Write the report

To `evals/results/reports/<YYYY-MM-DD>-<topic>.md`. Reports are never deleted —
the sequence is the project's memory, and a claim that quietly stops being true
between two reports is itself a finding.

Structure is yours; these properties are not:

- **Every claim cites its evidence.** `run_id`, and where inside it. Not "the
  agent often edits before reading" but "`duration-notes_..._r2`: `write` fired
  at journal step 2 with an empty context (also r1, r3)". The reader must be
  able to open it and disagree.
- **At most three recommendations, each stating how it would be measured.** A
  recommendation whose measurement you cannot state does not get made. Name the
  category it targets and its count.
- **A section for what the evidence cannot support** — the claims you wanted to
  make and could not. This is the one that keeps the report honest.
- **An empty `## Comments` section at the end**, for the human to write into.
  Read the previous report's comments before writing a new one, and say where
  you acted on them.

## 6. Declare your own conflict

Claude Code writes this harness and also analyses it, so a blind spot in the
design is a blind spot in the diagnosis. When a recommendation would validate a
design choice you (or a previous report) made, say so in that line. The human's
comments are the only external check.

## What not to do

- Do not grade prose quality — that is J1's job, and J1 does not exist yet.
- Do not rank configurations on pass rate at n<10.
- Do not propose a topology change to fix a `reasoning`-class failure. Routing,
  role splits and context partitioning address retrieval, tooling and stopping;
  if the measured bottleneck is judgement, say that instead of redesigning.
- Do not smooth over a crash or a tampered run by averaging it into a rate.
  Report it separately.
