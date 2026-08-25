[← Wiki index](README.md)

# 11. Evaluation status

*The running state. This is the one page in the wiki expected to change often —
everything else describes design; this describes today.*

**Last updated: 2026-07-29.** Update it when a phase lands, a scenario is added,
or a comparison is decided. A status page nobody updates is worse than none.

## 11.1 One-paragraph summary

The pipeline works end to end. It has **one scenario**, at the easiest
difficulty level, so it cannot yet compare two configurations — and half of the
runs so far crashed on an unrelated infrastructure bug. Nothing measured today
says anything about the agent's capability. The next moves are: fix the crash,
author scenarios, re-baseline.

## 11.2 What's built

| Phase | State | What it covers |
| --- | --- | --- |
| **P0** trace capture | **done** | `EVAL_TRACE_FILE` makes the agent append one JSON object per LLM/tool event ([trace.py](../agent/trace.py)). Every automatic metric is a sum over that file. |
| **P1** runner | **done** | [evals/](../evals/): materialize → run → verify → integrity → record, plus `validate` and `show`. Metrics automatic, including the failure taxonomy. |
| **P2** judge | not started | `claude -p` with a pinned rubric and diff-hash cache. Quality scoring is manual until then. |
| **P3** compare/report | not started | Leaderboard and written comparisons. `show` covers the basics today. |
| **P4** scenario library | **1 of ~15** | Only one L0 scenario exists. **This is the main gap.** |
| **P5** SWE-bench L3 | not started | Needs Docker. Deliberately last. |

## 11.3 Where the numbers stand

**One scenario, one configuration, n=2.** This establishes the pipeline works
end to end. It is not yet a measurement of anything about the agent — see
[8.6](08-evaluation-method.md#86-fair-comparison) for why nothing should be
concluded at this sample size.

| config | runs | pass | rate | calls | bounces | failure classes |
| --- | --- | --- | --- | --- | --- | --- |
| `baseline` | 2 | 1 | 50% | 10.5 | 6.5 | stopping=1 |

Per-run detail:

| | rep 1 | rep 2 |
| --- | --- | --- |
| outcome | pass | crash (`stopping`) |
| steps | 6 | 5 |
| provider calls | 11 | 10 |
| failover bounces | 4 | 8 |
| tokens in | 89,260 | 55,126 |
| distinct models | 5 | 7 |
| wall time | 21.6s | 12.7s |

Raw evidence in `evals/results/runs/`; the ledger row is in
[evals/CONFIGS.md](../evals/CONFIGS.md).

Two observations, both n=2, and both about the **harness** rather than the
agent:

1. **The one failure was infrastructure, not capability** — see blockers below.
2. **A one-line fix costs ~10 provider calls across 5–7 distinct models and tens
   of thousands of input tokens.** Failover works, but the pool is walked hard
   for trivial work. Worth understanding before reading anything into efficiency
   numbers.

## 11.4 Blockers

| Issue | Impact | State |
| --- | --- | --- |
| Groq returns `404 model_not_found` for `meta-llama/llama-4-scout-17b-16e-instruct`; 404 isn't in the router's transient set, so it propagates and kills the run | ~50% of runs crash for a reason unrelated to the task, which makes any pass rate meaningless | Flagged as separate work; fix on a branch and measure it ([4.6](04-failover.md#46-known-gaps)) |
| Only one scenario, at L0 | Nothing to compare configurations on. L0 is a canary, not a comparison instrument ([9.7](09-scenarios.md#97-the-difficulty-ladder)) | P4 |
| `agent_evals` local history has diverged from its GitHub remote after the restructure | Scenarios aren't backed up | Needs a force-push decision |

## 11.5 What to do next

1. **Fix the dead-model crash.** Nothing measured is trustworthy while half the
   runs die on it. It's a candidate change like any other: branch, add a
   configuration, measure against baseline.
2. **Author scenarios** — L1 and L2, across the categories in
   [9.6](09-scenarios.md#96-categories-to-cover). The comparison instrument is
   whatever currently lands between roughly 20% and 80% pass rate; L0 alone
   can't distinguish two configurations.
3. **Re-baseline at n=5** once those two are done, and record it in the ledger.
4. **P2, the judge** — worth building only once there are enough scenarios that
   reading diffs by hand hurts.

---

**Previous:** [← 10. Metrics](10-metrics.md) · **Next:** [12. Development harness →](12-development-harness.md)
