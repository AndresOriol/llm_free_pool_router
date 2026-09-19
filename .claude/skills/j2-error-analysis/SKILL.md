---
name: j2-error-analysis
description: Run a J2 batch analysis over recorded eval runs - open-code every failure, group them into a failure taxonomy with counts, and write a report that cites its evidence and ranks what to change next. Use when eval runs have been recorded and you need to know what to fix, when asked to "run J2", "analyse the batch", "do the error analysis", or after any batch of runs completes.
---

# J2 — batch error analysis

You are the **analyst**, not the grader. Your output is not a score. It is
*insight*: what went wrong, how often, and what to change first, written so
the human can check you by opening the evidence you cite.

This is the expensive paid call in the loop, and the only tool here stronger
than the system it studies. Spend it on judgement, not on moving files around.

**Where it sits.** J2 is the batch step of the `behaviour-change` loop: a batch
of runs → one `trace-reviewer` review per failed run → **J2 over the reviews** →
the top recommendation becomes a probe and a change. How to read a run, and
every trap in doing so, is in
[docs/evaluation/reading-runs.md](../../../docs/evaluation/reading-runs.md).
Read it first. This file does not repeat it.

## What is being decided

There is one agent configuration, `code`
([One conversation, on the pool](../../../docs/agents/code.md#one-conversation-on-the-pool)):

```bash
python -m evals run --config code --reps 3
```

**Cost is being tested, not only correctness.** The conversational loop lost
the last comparison, 226,854 input tokens to 5,756, and the arm that won was
retired without a re-run
([The cost result is the one that replicated](../../../docs/agents/code.md#the-cost-result-is-the-one-that-replicated)).
Compare against that 5,756 and against this batch's own history. A report that
gives pass rates and says nothing about tokens has answered the wrong question.

## 1. Get the evidence

```bash
python -m evals bundle --out <scratch>/j2-bundle.md     # --config NAME, --since 20260806T000000Z
```

The bundle is ordered failures first, and every section is labelled with its
`run_id`. It includes the diff, the hidden-test output and the stdout tail, but
not `trace.json` or `stderr.log`. Open each run directory yourself.

**Read `evals/results/reviews/<run_id>.md` first, where one exists.** A review
is the open-coding note for that run, written from the full evidence. It is a
claim, not a fact. Where you disagree, say so and cite what it missed. **For a
failed run with no review, spawn `trace-reviewer`** (one per run, in parallel)
rather than working from the bundle's excerpt.

Say which runs had a non-empty `trace.jsonl` and which had a `trace.json`,
because traps 2 and 3 decide which numbers you can quote.

## 2. Open-code every failure

One note per failed run, in your own words, before any categorising. Be
specific. Not "reasoning error", but "changed the header string instead of
making the lookup case-insensitive". Where a review exists, its "Where it
turned" section is that note. Check it against the evidence and carry it
forward.

Read **every** failure, not a sample. Runs here are scarce and expensive, so
the problem is having enough varied failures, not choosing among them.

For each one, check the three things only the evidence shows: whether the
agent's account matches the diff (trap 1), whether each claim traces to a
command that actually ran, and whether the docs were updated. A code change
that leaves a doc stale is a failed session, not a pass with a nit.

## 3. Group them (axial coding)

Cluster the notes into named categories and count them.

- A category needs **at least two instances**. Name one-offs in the roll-call
  and leave them uncategorised.
- **Never pool across architectures** (trap 7).
- **Split an existing class rather than inventing a parallel one.** The
  taxonomy in [Metrics](../../../docs/evaluation/metrics.md#failure-taxonomy)
  once put 12 of 13 failures in `reasoning`, which renames "failed" instead of
  diagnosing it. Subdividing it is the standing job, and `stopping` splits four
  ways (trap 4).
- Each category names **what would fix it**, as a rung of the extension ladder
  in the `deepagents` skill, with the file. A category whose fix is "be
  smarter" is not a category.

## 4. Compute the verdict mechanically

Promote, draw or reject comes from
[the promotion rule](../../../docs/evaluation/method.md#the-promotion-rule),
not from your reading. State it before any narrative, so the narrative cannot
colour it.

Pass rates at these sample sizes are noise (trap 6). If cost cannot be read out
of this batch (trap 2), **say the batch does not settle the question**, and make
fixing the measurement the first recommendation. Do not substitute a
pass-rate ranking.

## 5. Write the report

To `evals/results/reports/<YYYY-MM-DD>-<topic>.md`. Reports are never deleted.
The sequence of reports is the project's memory.

- **Every claim cites its evidence**: the `run_id`, and where inside the run.
  The reader must be able to open it and disagree.
- **Counts from different architectures are never merged** without saying so
  and why.
- **At most three recommendations, ranked.** Each names its category and count,
  its rung and file, and **how it would be measured**:
  - For a behavioural change, name the run, the turn `n` and the agent that
    `behaviour-change` §2 would freeze as the first probe. If no run in the
    category has a `trace.json`, recording one is part of the recommendation.
  - For a fault in the instrument or the infrastructure, name the metric or run
    that would show it fixed.
  A recommendation whose measurement you cannot state is not made.
- **A section for what the evidence cannot support**: the claims you wanted to
  make and could not.
- **An empty `## Comments` section at the end**, for the human to write in.
  Read the previous report's comments before writing a new one, and say where
  you acted on them.

## 6. Declare your own conflict

Claude Code writes this harness and also analyses it, so a blind spot in the
design is a blind spot in the diagnosis. When a recommendation would validate a
design choice you or a previous report made, say so in that line. The specific
one to watch: the arm that shipped was ported from someone else's SDK, and the
one built here was winning on cost when it was deleted. If the numbers say the
deletion cost something real, say so plainly.

## What not to do

- Do not grade prose quality. That is J1's job, and J1 does not exist.
- Do not propose a sub-agent or role split for a judgement failure
  ([Judgement, not topology](../../../docs/evaluation/reading-runs.md#judgement-not-topology)).
- Do not smooth a crash, a hang or a tampered run into a rate. Report it
  separately.
- Do not start the fix. Hand the top recommendation to `behaviour-change`.
