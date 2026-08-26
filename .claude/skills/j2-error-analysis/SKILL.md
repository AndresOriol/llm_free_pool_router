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
measurement is not "did this prompt change help" but "which of two agent
architectures to keep" ([6.1](../../../docs/06-agent.md#61-two-architectures-one-question)):

| Arm | Config | What it is | What it leaves behind |
| --- | --- | --- | --- |
| Narrow roles | `session`, `context-and-gate`, `harness-v*` | A LangGraph state machine of narrow roles over a shared log, briefs pushed down | `journal.jsonl`, `steps/NN-<role>.md`, `rationale.md`, `trace.jsonl` |
| **Conversational** | `deepagents` | One `create_deep_agent` conversation, SDK-compacted, on the pool's wide members | `trace.json` (a LangSmith run tree) when tracing is on, and `stderr.log` |

They are run interleaved and must be analysed together:

```bash
python -m evals run --config session --config deepagents --reps 3
```

**A draw keeps the simpler configuration**, and "simpler" means the one this repo
does not have to maintain — the conversational arm
([8.7](../../../docs/08-evaluation-method.md#87-the-promotion-rule)). Say which way
a draw falls rather than reporting it as "no result".

**The claim under test is cost, not only correctness.** The conversational loop
lost the last comparison 226,854 input tokens to 5,756, and this batch is the
re-run against an SDK that now ships summarization and a pool whose floor is
128,000 tokens ([6.8.1](../../../docs/06-agent.md#681-the-cost-result-is-the-one-that-replicated)).
A report that ranks the arms on pass rate and says nothing about tokens has
answered the wrong question.

## 1. Get the evidence

```bash
python -m evals bundle --out /tmp/j2-bundle.md
```

Options: `--config NAME` (repeatable) to scope to one configuration, `--since
20260806T000000Z` to scope to a batch. Read the bundle. It is ordered
failures-first and every section is labelled with its `run_id`.

The bundle carries the diff, the hidden-test output, the agent stdout tail and —
for narrow-role runs only — the session rationale. It does **not** carry
`trace.json` or `stderr.log`, so for any `deepagents` run in the batch you must open
the run directory yourself. Do that for every one of them; the bundle's clip is not
enough on that arm.

**Read `evals/results/reviews/<run_id>.md` first, where one exists.** Those are
per-run post-mortems written by the `trace-reviewer` subagent: the trajectory, the
turn a run could not recover from, and what context that turn had. They are the
open-coding notes for the runs they cover, done properly and against the full
evidence rather than the bundle's clip.

A review is a claim, not a fact. Where you disagree with one, say so and cite what
it missed — and if a run in this batch has no review, spawn `trace-reviewer` for it
rather than substituting the bundle's clip.

### The trap that will ruin this report if you miss it

`evals/metrics.py` derives every trace-based metric from `trace.jsonl`, which
**only the narrow-role arm writes**. On a `deepagents` run, `provider_calls`,
`tokens_in`, `tokens_out`, `steps`, `tool_calls`, `bad_tool_calls`, `models_used`,
`ran_own_tests` and `self_corrected` are zero or empty *by construction*, whatever
the run did.

So the bundle's summary table will show the conversational arm making zero calls
and spending zero tokens against the narrow arm's real numbers. **That is not a
39× win reversed; it is an instrument that is not plugged in.** Any comparison of
the two arms on those columns is invalid, and saying so is itself one of this
report's findings.

Until that is fixed, count a `deepagents` run's calls, model mix and cost curve
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

- **Does the agent's own account match the diff?** On the narrow arm that is
  `rationale.md`; on the conversational arm it is the final message in
  `stdout.log`. A run claiming a change the diff does not contain is the single
  most important thing you can find, because the whole review model assumes it does
  not happen.
- **Does every claim in that account trace to a command that actually ran?** The
  journal has exit codes on the narrow arm; on the conversational arm you need the
  tool spans in `trace.json`, and without one you should say the claim is
  unverifiable rather than accept it.
- **Did the docs get updated?** A code change with a stale doc is a failed session,
  not a passing one with a nit.

**Split `stopping` before you count it.** It is `evals/metrics.py`'s class for every
timeout and crash, and on the conversational arm it hides at least four unrelated
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
- **Count per arm, and pool only when the cause is genuinely shared.** A category
  that can only occur on one architecture — a brief that carried no context, a
  summarization pass that dropped an established fact — is evidence about that arm
  and must not be averaged across both.
- Prefer **splitting an existing class over inventing a parallel one**. The current
  taxonomy (`retrieval` / `tooling` / `reasoning` / `stopping`, in
  `docs/10-metrics.md`) put 12 of 13 failures in `reasoning`, which is a rename of
  "failed" rather than a diagnosis. Subdividing it is the standing job, and
  `stopping` now needs the same treatment on the conversational arm.
- Each category must name **what would fix it**, and the mechanism differs by arm:
  the narrow arm's levers are roles, briefs and blackboard sections; the
  conversational arm's are `agent/deep/system_prompt.md`, `context.py`, the shell
  allowlist, and the knobs in `session.py` (`CONTEXT_FLOOR`, `RECURSION_LIMIT`, the
  middleware and subagent lists). A category whose fix is "be smarter" is not a
  category.

## 4. Compute the verdict mechanically

Promote / draw / reject comes from the promotion rule in
`docs/08-evaluation-method.md#87-the-promotion-rule`, not from your reading. State
it before any narrative so the narrative cannot colour it.

**Pass rates at these sample sizes are noise.** The noise floor is around 15 points
at ~30 trials; one configuration in this repo scored 3/3 and 1/3 on consecutive
batches of the same scenario. Cost metrics replicate; pass rates do not. Say so
rather than ranking on them.

That makes cost the load-bearing measurement — which is exactly the one currently
unreadable on the conversational arm (§1). **If the cost comparison cannot be made
from this batch, say that the batch does not settle the question**, and make fixing
the instrument the first recommendation. Do not substitute a pass-rate ranking for
the comparison the arms exist to make.

## 5. Write the report

To `evals/results/reports/<YYYY-MM-DD>-<topic>.md`. Reports are never deleted — the
sequence is the project's memory, and a claim that quietly stops being true between
two reports is itself a finding.

Structure is yours; these properties are not:

- **Every claim cites its evidence.** `run_id`, and where inside it. Not "the agent
  often edits before reading" but "`threshold-off-by-one_…_deepagents_r1`:
  `stderr.log` shows 25 calls in 97s and the first edit at 08:32:41, before any read
  of `alerts/rules.py`". The reader must be able to open it and disagree.
- **Per-arm counts are never merged into a single rate** without saying you merged
  them and why.
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

Claude Code writes this harness and also analyses it, so a blind spot in the design
is a blind spot in the diagnosis. When a recommendation would validate a design
choice you (or a previous report) made, say so in that line. The human's comments
are the only external check.

There is a specific version of this to watch for now: the narrow-role arm was built
here and the conversational arm was ported from someone else's SDK. A verdict that
keeps the thing this repo authored deserves the loudest version of this
declaration — and note that the promotion rule points the other way, since a draw
keeps the code we do not maintain.

## What not to do

- Do not grade prose quality — that is J1's job, and J1 does not exist yet.
- Do not rank configurations on pass rate at n<10.
- Do not compare the arms on `tokens_in`, `provider_calls` or `models_used` out of
  `run.json`. Those are zero on every `deepagents` run for instrumental reasons (§1).
- Do not propose a topology change to fix a `reasoning`-class failure. Routing, role
  splits and context partitioning address retrieval, tooling and stopping; if the
  measured bottleneck is judgement, say that instead of redesigning. On the
  conversational arm this arrives disguised as "give it a subagent for that".
- Do not smooth over a crash, a hang or a tampered run by averaging it into a rate.
  Report it separately.
