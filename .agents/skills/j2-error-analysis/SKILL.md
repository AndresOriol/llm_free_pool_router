---
name: j2-error-analysis
description: Run a J2 batch analysis over recorded eval runs - open-code every failure, group them into a failure taxonomy with counts, and write a report that cites its evidence and ranks what to change next. Use when eval runs have been recorded and you need to know what to fix, when asked to "run J2", "analyse the batch", "do the error analysis", or after any batch of runs completes.
---

# J2 — batch error analysis

You are the **analyst**, not the grader. Your output is not a score. It is
*insight*: what went wrong, how often, and what to do about it — written so the
human can check you by opening the evidence you cite.

This is the expensive paid call in the loop and the only instrument stronger than
the system it studies, so spend it on judgement and not on file-wrangling.

## What a batch is now

Batches are **two-armed**. The question this project is currently deciding by
measurement is not "did this prompt change help" but "what does the one
surviving architecture actually cost"
([6.1](../../../docs/06-agent.md#61-one-conversation-on-the-pool)).

There is **one** agent configuration, `code`: a single `create_deep_agent`
conversation, SDK-compacted, on the pool's wide members. It leaves behind
`trace.jsonl` (the flat event log every automatic metric is summed over),
`trace.json` (the condensed run — a header, then `turns`) when tracing is on, and
`stderr.log`.

```bash
python -m evals run --config code --reps 3
```

The narrow-role arm — `session`, `context-and-gate`, `harness-v*`, which wrote
`journal.jsonl`, `steps/NN-<role>.md` and `rationale.md` — was **deleted**
([6.1.1](../../../docs/06-agent.md#611-the-arm-that-was-deleted)). Old runs of it
are still on disk and still analysable; nothing produces new ones.

**The claim under test is cost, not only correctness.** The conversational loop
lost the last comparison 226,854 input tokens to 5,756, and the arm that won was
retired without the re-run ever happening
([6.4.1](../../../docs/06-agent.md#641-the-cost-result-is-the-one-that-replicated)).
There is no second arm to rank against any more, so the comparison is against
that recorded 5,756 and against the batch's own history. A report that reports
pass rates and says nothing about tokens has answered the wrong question.

## 1. Get the evidence

```bash
python -m evals bundle --out /tmp/j2-bundle.md
```

Options: `--config NAME` (repeatable) to scope to one configuration, `--since
20260806T000000Z` to scope to a batch. Read the bundle. It is ordered
failures-first and every section is labelled with its `run_id`.

The bundle carries the diff, the hidden-test output, the agent stdout tail and —
on old narrow-role runs only — the session rationale. It does **not** carry
`trace.json` or `stderr.log`, so you must open each run directory yourself. Do
that for every run; the bundle's clip is not enough.

**Read `evals/results/reviews/<run_id>.md` first, where one exists.** Those are
per-run post-mortems written by the `trace-reviewer` subagent: the trajectory, the
turn a run could not recover from, and what context that turn had. They are the
open-coding notes for the runs they cover, done properly and against the full
evidence rather than the bundle's clip.

A review is a claim, not a fact. Where you disagree with one, say so and cite what
it missed — and if a run in this batch has no review, spawn `trace-reviewer` for it
rather than substituting the bundle's clip.

### The trap that will ruin this report if you miss it

`evals/metrics.py` derives every trace-based metric from `trace.jsonl`. That file
**used not to be written by this arm at all**: the JSONL callback handler was only
attached when the narrow-role arm was retired, in `agent/code/session.py`.

So on any `code` run recorded before that, `provider_calls`, `tokens_in`,
`tokens_out`, `steps`, `tool_calls`, `bad_tool_calls`, `models_used`,
`ran_own_tests` and `self_corrected` are zero or empty *by construction*, whatever
the run did. **A zero is ambiguous, and reading one as cheapness inverts the
single result this whole comparison turns on.** Check whether the run directory
has a non-empty `trace.jsonl` before quoting any of those columns, and say which
runs in the batch had one.

Where it is missing, count the run's calls, model mix and cost curve
from the router's narration in `stderr.log` — one timestamped `Routing to <account>
(model=…, ~N tok)` line per provider call:

```bash
grep -c 'Routing to' evals/results/runs/<run_id>/stderr.log
```

State in the report that those counts are hand-derived and how.

## 2. Open-code every failure

One note per failed run, in your own words, before any categorising. Say what went
wrong *specifically* — not "reasoning error" but "changed the header string instead
of making the lookup case-insensitive". Where a review exists, its "Where it turned"
section is that note; your job is to check it against the evidence and carry it
forward, not to redo it.

Read **every** failure, not a sample. Industry practice samples ~100 traces out of
thousands; here runs are scarce and expensive, so the ratio inverts and the binding
constraint is having enough varied failures, not choosing among them.

While reading, check three things that only show up in the evidence:

- **Does the agent's own account match the diff?** That is the final message in
  `stdout.log` (on an old narrow-role run, `rationale.md`).
  A run claiming a change the diff does not contain is the single
  most important thing you can find, because the whole review model assumes it does
  not happen.
- **Does every claim in that account trace to a command that actually ran?** You
  need the `tool_results` on a turn in `trace.json`, or the `tool_end` events in
  `trace.jsonl`; without either, say the claim is unverifiable rather than
  accept it.
- **Did the docs get updated?** A code change with a stale doc is a failed session,
  not a passing one with a nit.

**Split `stopping` before you count it.** It is `evals/metrics.py`'s class for every
timeout and crash, and it hides at least four unrelated
causes: a loop that never settled (`GraphRecursionError`), an agent working steadily
that ran out of clock, **a single provider call that hung** — one recorded run made
2 calls in 2722 seconds — and a starved pool waiting for a wide member. The last gap
between `Routing to` lines in `stderr.log` separates them. Counting them together
would put an infrastructure fault and a capability fault in the same row, and the
fixes have nothing to do with each other.

## 3. Group them (axial coding)

Cluster the notes into named categories and count them. Rules:

- A category needs **at least two instances**. One occurrence is an anecdote; name
  it in the roll-call and leave it uncategorised.
- **Never pool a category across architectures.** If the batch includes old
  narrow-role runs, a cause that can only occur on one of them — a brief that
  carried no context, a summarization pass that dropped an established fact — is
  evidence about that architecture alone and must be counted separately.
- Prefer **splitting an existing class over inventing a parallel one**. The current
  taxonomy (`retrieval` / `tooling` / `reasoning` / `stopping`, in
  `docs/10-metrics.md`) put 12 of 13 failures in `reasoning`, which is a rename of
  "failed" rather than a diagnosis. Subdividing it is the standing job, and
  `stopping` needs the same treatment.
- Each category must name **what would fix it**, and the levers are
  `agent/code/system_prompt.md`, `context.py`, the shell allowlist, and the knobs
  in `session.py` (`CONTEXT_FLOOR`, `RECURSION_LIMIT`, the middleware and
  subagent lists). A category whose fix is "be smarter" is not a category.

## 4. Compute the verdict mechanically

Promote / draw / reject comes from the promotion rule in
`docs/08-evaluation-method.md#87-the-promotion-rule`, not from your reading. State
it before any narrative so the narrative cannot colour it.

**Pass rates at these sample sizes are noise.** The noise floor is around 15 points
at ~30 trials; one configuration in this repo scored 3/3 and 1/3 on consecutive
batches of the same scenario. Cost metrics replicate; pass rates do not. Say so
rather than ranking on them.

That makes cost the load-bearing measurement, and on older runs it is the one that
is unreadable (§1). **If cost cannot be read out of this batch, say that the batch
does not settle the question**, and make fixing the instrument the first
recommendation. Do not substitute a pass-rate ranking for it.

## 5. Write the report

To `evals/results/reports/<YYYY-MM-DD>-<topic>.md`. Reports are never deleted — the
sequence is the project's memory, and a claim that quietly stops being true between
two reports is itself a finding.

Structure is yours; these properties are not:

- **Every claim cites its evidence.** `run_id`, and where inside it. Not "the agent
  often edits before reading" but "`threshold-off-by-one_…_code_r1`:
  `stderr.log` shows 25 calls in 97s and the first edit at 08:32:41, before any read
  of `alerts/rules.py`". The reader must be able to open it and disagree.
- **Counts from different architectures are never merged into a single rate**
  without saying you merged them and why.
- **At most three recommendations, each stating how it would be measured.** A
  recommendation whose measurement you cannot state does not get made. Name the
  category it targets and its count.
- **A section for what the evidence cannot support** — the claims you wanted to make
  and could not. This is the one that keeps the report honest, and on a batch with
  no `trace.json` it will be long.
- **An empty `## Comments` section at the end**, for the human to write into. Read
  the previous report's comments before writing a new one, and say where you acted
  on them.

## 6. Declare your own conflict

Codex writes this harness and also analyses it, so a blind spot in the design
is a blind spot in the diagnosis. When a recommendation would validate a design
choice you (or a previous report) made, say so in that line. The human's comments
are the only external check.

There is a specific version of this to watch for now. The arm that shipped was
ported from someone else's SDK, and the arm it replaced was built here and was
winning on cost when it was deleted
([6.1.1](../../../docs/06-agent.md#611-the-arm-that-was-deleted)). That decision is
not yours to re-litigate in a report — but a reading that quietly excuses the cost
it accepted is exactly the blind spot to declare. If the numbers say the deletion
cost something real, say so plainly.

## What not to do

- Do not grade prose quality — that is J1's job, and J1 does not exist yet.
- Do not rank configurations on pass rate at n<10.
- Do not quote `tokens_in`, `provider_calls` or `models_used` out of `run.json`
  without checking the run has a non-empty `trace.jsonl`. On runs recorded before
  that file was wired up they are zero for instrumental reasons (§1).
- Do not propose a topology change to fix a `reasoning`-class failure. Routing, role
  splits and context partitioning address retrieval, tooling and stopping; if the
  measured bottleneck is judgement, say that instead of redesigning. Here it
  arrives disguised as "give it a subagent for that".
- Do not smooth over a crash, a hang or a tampered run by averaging it into a rate.
  Report it separately.
