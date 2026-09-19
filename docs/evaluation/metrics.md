[← Wiki index](../README.md)

# Metrics

*What gets measured, what each number is for, and why they're never collapsed
into one score.*

## The axes

Metrics are grouped by the question they answer. Keeping the grouping explicit
is what stops a comparison from being read as "number went up".

| Axis | Metrics | What it's for |
| --- | --- | --- |
| **Task success** | `verified`, `f2p_ratio`, `p2p_ratio` | The only gating axis. `verified` is the *capability* half of `outcome`, and it is what the results table counts — see [Why the ledger counts `verified`](#why-the-ledger-counts-verified-and-not-outcome). |
| **Diagnosis** | `failure_class` | What to actually work on next. |
| **Autonomy** | `ran_own_tests`, `self_corrected` | Closing its own loop is the difference between an agent and a code generator. |
| **Efficiency** | `provider_calls`, `failover_bounces`, `tokens_*` | On a free pool this *is* the cost model. A run that passes but drains the pool is a weak pass. |
| **Robustness to failover** | `models_used` vs `outcome` | Did switching model mid-task derail it? |
| **Integrity** | `tampered`, `extended_files` | Non-negotiable, tracked separately so it can never be averaged away. Only a *weakening* counts; strengthening a protected suite is the second column. |
| **Quality** | Judge scores | Right for the right reason, and in the right scope. |

### Why the ledger counts `verified` and not `outcome`

`outcome` is one label over two questions that do not share a scale — *did it
solve the task*, and *did it respect what it was told not to touch*. A run that
flips every hidden test and weakens a protected file is recorded `tampered`,
because integrity outranks everything else in the verdict, and it should. But
the results table then read that as a failed task, and there is no rate that
can hold both meanings: a version that solves nothing and a version that solves
everything by rewriting the tests are both bad, differently.

The first full-set batch is what made this concrete. It recorded 3/9 while
seven of the nine runs left every hidden test green, and `verified: true` was
sitting in each of those records with nothing rendering it. The correction is
not confined to that batch — splitting the columns moves `context-and-gate`
from 1/4 to 3/4 and `deepagents` from 0/1 to 1/1 in three separate rows.

So the tables report **solved** (`verified`) and **integrity** in separate
columns and never combine them. A weakening still makes the run's `outcome`
`tampered`; what changed is that it is no longer also counted as a task the
agent could not do.

Two things the same tables now refuse to average:

- **Verdicts from before the integrity oracle changed.** That oracle hashed the
  file, so it could not tell an appended regression test from a deleted
  assertion. Such a record has no `extended_files` key at all, which makes the
  field's absence an exact marker for which evaluator scored it. `config_sha`
  pins the agent; nothing yet pins the harness, which is why this has to be
  inferred.
- **Runs with no trace.** Everything summed over `trace.jsonl` records zero
  when the trace never arrived, and a zero is indistinguishable from a
  measurement. Cost means are taken over the traced runs only, and the count of
  the rest is printed beside them.

## Automatic metrics

Token totals exclude a router wrapper's usage only when the corresponding
provider usage is also present: the wrapper repeats the provider's metadata.
Wrapper-only legacy/direct traces retain their reported usage. The September 10
Machintl research traces exposed a double count when both were summed.
Recompute older totals from their JSONL before comparing costs; existing stored
run summaries and historical reports are not rewritten by this correction.

Every one is a count or sum over `trace.jsonl`, the diff, and the verification
output ([The local trace](observability.md#the-local-trace)).

| Metric | Definition |
| --- | --- |
| `outcome` | `pass` \| `fail` \| `crash` \| `timeout` \| `tampered` |
| `f2p_ratio` | `fail_to_pass` tests flipped / total — partial credit on the fix |
| `p2p_ratio` | `pass_to_pass` tests still passing — collateral damage |
| `failure_class` | `retrieval` \| `tooling` \| `reasoning` \| `stopping` — see below |
| `steps` | Agent loop iterations (tool-call rounds) |
| `provider_calls` | LLM calls, including failover retries |
| `failover_bounces` | Transient failures before a step succeeded — wasted quota |
| `tokens_in` / `tokens_out` | Summed where the provider reports usage |
| `tokens_per_call` | `tokens_in` over provider calls — what one step costs. Separates a long run from an expensive one, which `tokens_in` alone cannot ([The arm that was deleted](../agents/code.md#the-arm-that-was-deleted)) |
| `edited_nothing` | No edit tool was called all run. See 10.2.4 |
| `stub` | Was this a stub rather than an agent — stubs are excluded from the ledger |
| `traced` | Did `trace.jsonl` arrive. Everything summed over the trace reads 0 when it did not, which is indistinguishable from a measurement |
| `bounce_models` | Bounces per model — which member is spending the pool's time |
| `retired_models` | Members dropped mid-run because they are gone upstream. See 10.2.3 |
| `bounces_per_call` | Failover bounces over provider calls. A quota and latency figure, **not** a cost one: a refused call carries no input tokens |
| `tampered_files` | Protected files the agent weakened — see [The run lifecycle](method.md#the-run-lifecycle) for what makes a change a weakening |
| `broken_files` | Protected files the run left unrunnable — it removed nothing, it wrote something that does not run. Classified `tooling`, never integrity |
| `lost_invariants` | Documented guarantees the run deleted — `"<page>: <phrase>"`, declared per scenario in `doc_invariants` ([`immutable` or `doc_invariants`?](scenarios.md#immutable-or-doc_invariants)) |
| `extended_files` | Protected files it changed *without* weakening: it appended to a suite it was told not to break, and the original assertions still hold. Recorded, never scored |
| `bad_tool_calls` | Invalid tool name, failed `edit_file`, malformed args. **Reads zero on every run today** — the trace discards the tool's status ([Blockers](../status.md#blockers)) |
| `models_used` | Distinct models that served a step, and the per-model call mix |
| `ran_own_tests` | Did the agent invoke `execute` on the test command itself |
| `added_tests` | Test functions the run added that nothing asked for — see [Unprompted tests](#unprompted-tests) |
| `wrote_account` | Did the run append to the project's `NOTES.md` — `null` where the scenario ships none. See 10.2.1 |
| `self_corrected` | Did a failing `execute` get followed by another edit |
| `files_touched` / `diff_lines` | Change size, vs the reference solution's size |
| `wall_time_s` | End to end |

### The account, and why it is only counted

[R7](../design/long-run-harness.md) is *"notes in, notes out: the session reads the
project's feedback file and appends its account to it. This is the whole human
interface."* Phase 2's north star is an agent that writes an account a human
reviews instead of the code, and until now nothing recorded whether one existed.

`wrote_account` is lines added to `NOTES.md`, off the diff. Deterministic, zero
tokens, no new scenario. It is `null` where the seed ships no feedback file,
because a scenario that never offered one cannot have skipped it — counting
those as failures would make the number improve every time such a scenario is
added.

**The baseline, over 40 runs: 7 of the 23 runs that solved their task also wrote
an account.** Four scenarios have never produced one —
`model-v3-propagation` in six runs, `bots-to-base-class`, `stock-export` and
`cover-the-rejections` in two each.

**Read that as a measurement, not yet as a verdict.** The standing session prompt
says to read `NOTES.md` and do what the newest feedback asks, then update any
documentation the change makes wrong. It does not say to append an account. So
these runs are not disobeying an instruction; they are declining an unstated
expectation, and closing that gap is a prompt change to be measured like any
other rather than a scoring change to be imposed. Nothing gates on this metric —
it exists so the question has a number attached before anyone argues about it.

### Unprompted tests

`added_tests` counts `def test_…` lines the diff **adds**, and only inside a file
pytest would collect — a test moved between files is not a new test, and one
written into a module that never runs is not a test at all.

**27 of 40 recorded runs added at least one; 58 tests in total.** In
`stale-categories` and `bots-to-base-class` the added tests pinned exactly the
bug and the invariant under test.

This is the behaviour a standing maintainer most needs, it happens in roughly
two runs out of three, and until [The run lifecycle](method.md#the-run-lifecycle) learned to tell
a strengthened protected file from a weakened one, the only thing the harness
ever did with it was score it as tampering. Recorded, not scored: a count of
tests says nothing about whether they assert anything, and rewarding the number
is how you buy assertions of `True`.

### Reading `failover_bounces`

The number alone says the run was long. It was recorded and never read, and what
it hid was worth reading: in a 40-run batch, **135 of 247 bounces were one model
answering 404** — `gemini-2.5-flash`, five times per run, once per account, in
every run.

The router handled it correctly the whole time. It recognises the wrapped 404,
drops the member for the process, and logs at ERROR naming the model to delete,
so the run survives — which is the point of holding a pool. Nothing read the log.

That is the shape of the problem: a retirement **costs no tokens and fails no
run**, so no metric anyone looks at moves. `bounce_models` and `retired_models`
are recorded per run and the ledger names any retired member beside the bounce
count, because that column is the only place this can surface.

A bounce is a **quota and latency** figure, never a cost one. A refused call
carries no input tokens: the provider turns a 429 or a 404 away at the gate.

### The run that changed nothing and said otherwise

Three recorded runs finished with an **empty diff** and a closing message
reporting the work as done:

> Items 1, 2, and 3 ... do not conflict and **were implemented**.

> The non-conflicting part of the request ... **has been fully implemented and
> tested**.

> **Implemented Requirements**: Added `checked` ... Added `delta_total` ...

No edit tool was called in any of them. One had run the project's tests, which
passed, because the visible suite does not cover the fields it claimed to add —
so the one check that could have caught it confirmed it instead.

This is the only failure in the set that **reads as a success**. An over-decline
leaves an empty diff and a refusal, and a refusal looks like one. This leaves an
empty diff and a competent-sounding report of work, which is
[C4](../design/long-run-harness.md) exactly: *the review surface is the agent's own
account of itself*, and a confidently wrong rationale reads exactly like a
correct one. Under Phase 2, where a human reviews prose, it passes.

**`edited_nothing` is the half that can be trusted.** Whether the prose claims
completion needs a reader — a regex on it flagged two runs that were a
declination (*"cannot be implemented"*) and a plan (*"I **will** implement"*),
neither of which is a false claim. Whether an edit tool was called does not need
a reader. So the harness records the mechanical fact and `bundle` puts it first
in the evidence, where the reader is.

Five of the eight empty-diff runs recorded so far were ordinary `stopping`
failures that claimed nothing. The flag is a prompt to look, not a verdict.

## Failure taxonomy

**This is the highest-leverage metric in the set.** A pass rate tells you a
configuration is worse; it doesn't tell you what to fix. Since we know which
files the reference solution touches, every failed run can be classified
automatically from the trace — no transcript reading.

| Class | Signal | What it means | What fixes it |
| --- | --- | --- | --- |
| `retrieval` | Never read a file the gold patch touches | Couldn't find the code | Better search/navigation tools, or context injection |
| `tooling` | Read it, but `edit_file` calls failed to apply, or `tool_use_failed` | Knew what to change, couldn't express the edit | Change the edit format — line-anchored or whole-file rewrite instead of exact-string match |
| `reasoning` | Edits applied cleanly, tests still fail | Wrong fix | Stronger model tier; better prompt |
| `stopping` | Declared done without running tests, or repeated the same failing step | Loop problem | Prompt, forcing a self-test before finishing |
| `budget` | Ended `STOPPED (step budget spent)` without repeating itself | Ran out of steps doing real work, not looping | Raise `AGENT_STEP_BUDGET`, or split the task |

**Why it matters most here specifically.** On small models the `tooling` class
is likely to dominate. Groq already returns `tool_use_failed` carrying the
model's raw malformed output, which the router surfaces today
([Making a reroute visible](../pool/failover.md#making-a-reroute-visible)). If the taxonomy shows a
large share of failures are malformed edits rather than wrong reasoning, then
**changing the edit tool's format is a bigger win than any model or prompt
change** — and that is not a conclusion you would reach by staring at a pass
rate.

It also answers the "bad retrieval vs. bad editing" question that normally
requires trajectory inspection — cheaply, for every run.

## The judge

`claude -p` against a fixed rubric. The judge sees: the task prompt,
`diff.patch`, the verification output, `evaluation/solution.patch`, and
`evaluation/criteria.md`. It returns JSON scoring 0–4 on:

- **correctness beyond tests** — right for the right reason, or coincidence?
- **scope discipline** — did it change only what the task asked for?
- **code quality** — does it read like the surrounding code? (the `ponytail` bar)
- **instruction adherence** — did it follow explicit constraints in the brief?

Three rules make scores comparable across time:

1. **Blind** — the judge is not told which configuration produced the diff, and
   runs are shuffled before judging.
2. **Pinned** — the judge model id and rubric prompt version are recorded in
   every verdict. Changing either starts a **new comparison epoch**; you cannot
   compare judged scores across epochs, only re-judge.
3. **Diff-addressed** — verdicts are cached by diff hash, so identical outputs
   score identically and repeat judging is free.

The judge is deliberately **not** shown the agent's transcript. The model that
writes a convincing narrative and the model that writes a correct patch are not
the same model, and we're grading the patch.

Status: not yet built ([What's built](../status.md#whats-built)). Quality
scoring is manual until then.

## Ranking is lexicographic, not weighted

> success rate → integrity clean → judge quality → provider calls

Collapsing these into one number requires inventing weights, and the weights
would be doing the deciding. Keep the columns visible.

## What a run leaves behind

```
evals/results/runs/<run_id>/
  run.json         fingerprint, scenario tag, metrics, outcome
  config.yaml      the configuration file this run was launched from
  trace.jsonl      captured events
  stdout.log
  stderr.log
  diff.patch       untouched code → final workdir
  verify.txt       hidden-test output
  judge.json       rubric scores + judge model/prompt version
```

Each directory is self-contained and carries every field a leaderboard needs, so
a summary is a glob rather than a query against a shared file — and merging a
configuration branch can never conflict over results
([Where things live](method.md#where-things-live)).

It also keeps the *evidence* behind each row, which is the part hosted tracing
loses. It is gitignored — an artifact of a run, kept on the machine that made
it. What goes in the repo is the writing over it: conclusions in
`evals/results/reports/<date>-<topic>.md`, citing runs by id, and the one-line
verdict in the ledger, [evals/CONFIGS.md](../../evals/CONFIGS.md).
