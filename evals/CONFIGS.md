# Configuration ledger

Every agent configuration tried, with the verdict and the reason. Losing
changes are recorded too — knowing what didn't work is most of the value of
keeping the data at all, and it's what stops the same idea being re-tried every
few months.

Promotion rule (see [8.7](../docs/08-evaluation-method.md#87-the-promotion-rule)):
promote when no task regresses by more than one trial **and** either success
rate improves beyond interval overlap, or success rate holds flat while a
secondary metric improves materially. Anything else is a draw, and a draw keeps
the simpler configuration.

> **The configurations in this table no longer exist as files, and neither does
> the architecture most of them were variants of.** The narrow-role harness —
> every `harness-v*` row, `adhoc-harness`, `session` and `context-and-gate` —
> was deleted, leaving `evals/configs/code.yaml` as the only agent
> configuration ([6.1.1](../docs/06-agent.md#611-the-arm-that-was-deleted)).
> These rows stay because a ledger of what was tried and what it showed is the
> point of the file; `git log` has the code behind each name.
>
> **Read the cost column before concluding anything from this.** The deleted
> family holds every cheap result here. Retiring it was a decision about what to
> maintain, taken against the measurements rather than because of them.

| Config | Ref / SHA | Change | Suite | Result | Verdict |
| --- | --- | --- | --- | --- | --- |
| `baseline` | `master` | — | L0 `retry-after-case`, n=3 | 3/3 | reference point |
| `adhoc-harness` | `harness/adhoc-router` | Narrow per-role agents over a shared blackboard | L0, n=6 | 4/6, 15k tok | **draw** — keep measuring |
| `harness-v3-merged` | `harness/adhoc-router` | One `investigate` role replaces locate + inspect | L0, n=6 | 4/6, 10.5k tok | **draw** — its 3/3 did not replicate |
| `harness-v6-guarded` | `harness/adhoc-router` | Forced summary + no-note guard on edit | L0, n=3 | 2/3, **5.8k tok** | cheapest by far; undecided |
| `harness-v5-lean` | `harness/adhoc-router` | Seeded files + edit retry + two-tool locate | L0, n=3 | 1/3 | **dropped** |
| `harness-v2-seeded` | `harness/adhoc-router` | Glob the file list, skip `locate` | L0, n=3 | 0/3 | **dropped** |
| `harness-v7-orchestrated` | `harness/adhoc-router` | Hub and spoke; execution as an agent | L0, n=3 | 0/3, most calls | **dropped for this task shape** |
| `harness-v8-session` | `harness/adhoc-router` | A session: briefed roles, journal, branch, docs and rationale as deliverables | L1+L2 `session`, n=2 | 2/4 | **no verdict** — ran alone, no baseline |
| `code` (was `deepagents`) | `harness/deepagents` | A conversation instead of narrow roles: `create_deep_agent` on the pool behind a hard 128k context floor, configured like `deepagents-code` | — | **never run** | **the only configuration left** — no eval run recorded, so no verdict |
| `code-peers` | `harness/agent-protocol` | `code` plus one `delegate` tool: it can ask the web explorer for a report mid-task, over A2A on a local transport ([16](../docs/16-agent-protocol.md)). The explorer is LangChain's deep-research agent, ported ([15.8](../docs/15-explorer.md#158-the-deep-research-port)) | — | **never run** | no verdict — read `input_tokens` and `delegated_tasks` first, and interleave against `code` |


### code-peers, before any eval run (2026-08-27)

**It was two configurations for one commit and is now one.** `code-peers-deep`
held the deep-research explorer while `code-peers` held the grounded-Gemini one;
the latter is deleted, so the two would be the same run. The pre-deep arm is at
`89181a2` if it is ever worth pricing what the replacement cost.

Do not read that as a measured win. The replacement was chosen on a recorded
*trajectory* — 13 searches, 0 sources opened, one file written at the end, every
query keyword-shaped — against a reference implementation that forbids each of
those by construction. Nothing has compared the two on cost or on the quality of
what they wrote, and the deep arm plausibly costs more: it is two conversations
where the other was one.

The pair is a clean A/B by construction: with `AGENT_PEERS=` empty this branch's
agent has exactly the tools `code` has, so the only difference measured is the
`delegate` tool and the prompt section listing what it can reach
([16.7](../docs/16-agent-protocol.md#167-what-this-costs-and-what-is-unmeasured)).

Expect it to be the expensive arm. A delegation is a whole explorer session --
14-21 model calls -- billed to the same `EVAL_TRACE_FILE` as the caller, so
`input_tokens` here includes it and is not comparable to `code` without reading
`delegated_tasks` beside it. Nothing enforces a per-task budget yet; that number
is meant to be observed rather than invented
([16.6](../docs/16-agent-protocol.md#166-what-a-delegation-costs)).

The question is not really cost, though. It is whether the agent delegates on
questions that genuinely need outside knowledge, or reaches for the web on things
it could have answered by reading the repo -- which no automatic metric will say,
and the trajectories will.

### code, before any eval run (2026-08-25)

The arm exists and works; nothing about it is comparable to anything yet. Two
ad-hoc runs outside the runner — no scenario, no hidden tests — are recorded in
[docs/11-eval-status.md](../docs/11-eval-status.md#the-coding-agent-first-look-2026-08-25)
because they bear on whether the comparison is worth its quota.

The short version: both runs produced a correct minimal fix, and neither
spec-gamed. But at 134k–162k input tokens against the narrow-role harness's
5,756, **the cost gap did not close** — it is ~30% under the conversational
baseline that was deleted for exactly this reason, not an order of magnitude.
n=1 per task, different tasks, and short enough that summarization likely never
fired, so this is a reason to run the comparison carefully rather than a result.

**Run it interleaved against `session`, never alone** — that was
`harness-v8-session`'s mistake and it is why that row still says no verdict:

```bash
python -m evals run --config code --reps 3
```

### harness-v8-session, first batch (2026-08-07)

Full analysis in
[results/reports/2026-08-07-first-session-batch.md](results/reports/2026-08-07-first-session-batch.md).

Not a promotion decision: one configuration, no interleaved comparison. What it
established is that **both new scenarios discriminate** — each passed once and
failed once, which is precisely what `retry-after-case` stopped being able to do.

Three defects in the *instrument* were found by reading the evidence: `.git`
survived the diff prune (read-only objects, Windows), a clean "exhausted" was
recorded as `crash`, and every rationale reported "Files changed: 0". All fixed.
**No diff-derived metric from this batch is usable**; the hidden-test ratios and
the journals are.

### The harness family, 2026-08-06

Full write-up in
[docs/06-agent.md](../docs/06-agent.md#64-why-it-is-shaped-this-way). Three
things worth carrying forward:

**No promotion.** Nothing beat baseline on the gating axis, and nothing is
distinguishable from anything else. `harness-v3-merged` scored 3/3 in one batch
and 1/3 in the next on an identical configuration — direct evidence that one L0
task at n=3 sits inside the noise floor, exactly as
[docs/08-evaluation-method.md](../docs/08-evaluation-method.md#86-fair-comparison)
predicts. Any ranking read off these pass rates would be invented.

**The cost result is real.** Token and call counts replicated across every rep
and batch, with between-configuration spread far exceeding within-configuration
variance: `harness-v6-guarded` runs the task on 7.3 calls / 5,756 input tokens
against baseline's 20.0 / 226,854. A 39× reduction, on a pool where tokens are
the binding constraint.

**Every failure is `reasoning`** — 12 of 13, with one `stopping` and zero
`retrieval` or `tooling`. Every variant found the file, edited it, and ran the
tests; they got the fix conceptually wrong. Architecture changes cannot move
that number, which is the strongest available argument for spending the next
effort on scenarios and model tiering rather than on more topologies.

**Do not re-run these on L0.** They are exhausted as a comparison. The next
useful measurement is the same set against a scenario that can discriminate.

### baseline, first measurement (2026-07-29)

Two reps of one L0 task. Not a rate — n=2 establishes the pipeline works and
gives a first look at variance, nothing more.

| | rep 1 | rep 2 |
| --- | --- | --- |
| outcome | pass | crash (`stopping`) |
| steps | 6 | 5 |
| provider calls | 11 | 10 |
| failover bounces | 4 | 8 |
| tokens in | 89,260 | 55,126 |
| distinct models | 5 | 7 |
| wall time | 21.6s | 12.7s |

Two observations already worth acting on:

1. **Rep 2 crashed on a dead model, not on the task.** Groq returns
   `404 model_not_found` for `meta-llama/llama-4-scout-17b-16e-instruct`. A 404
   is not in the router's transient set, so it propagates and kills the whole
   run. A decommissioned model in the pool should be disabled permanently and
   skipped, the same way a rate-limited one is skipped temporarily — otherwise
   one stale config entry can end an unattended run.
2. **A one-line fix costs 5–7 distinct models and ~90k input tokens.** The
   failover machinery works, but the pool is being walked hard for a trivial
   task. Worth understanding before reading anything into efficiency numbers.

## Stub configurations

`stub-*` are not real configurations. They drive `evals/fake_agent.py` to
exercise the runner's own paths (pass, each failure class, tampering) without
spending free-tier quota. Always run them with `--results` pointing somewhere
throwaway so they never enter the real record.

```bash
python -m evals run --config stub-fix --config stub-noop --config stub-badedit \
  --config stub-lost --config stub-tamper --reps 1 --results /tmp/selftest
```
