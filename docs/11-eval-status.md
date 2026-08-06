[← Wiki index](README.md)

# 11. Evaluation status

*The running state. This is the one page in the wiki expected to change often —
everything else describes design; this describes today.*

**Last updated: 2026-08-06.** Update it when a phase lands, a scenario is added,
or a comparison is decided. A status page nobody updates is worse than none.

## 11.1 One-paragraph summary

The pipeline works end to end and has now run its first real comparison. Two
decommissioned Groq models were the cause of the old 50% crash rate; with them
removed from the eval pool the baseline passes **3/3**. It still has **one
scenario**, at the easiest difficulty level, so a 3/3-vs-2/3 result is far
inside the noise floor — the comparison machinery is trustworthy now, the
sample size is not. The next move is authoring scenarios at L1 and L2.

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

**One scenario, two configurations, n=3, interleaved on an identical pool.**

| config | runs | pass | calls | bounces | `tokens_in` |
| --- | --- | --- | --- | --- | --- |
| `baseline` | 3 | 3/3 | 20.0 | 6.5 | 226,854 |
| `adhoc-harness` | 6 | 4/6 | 16.8 | 4.2 | 15,067 |
| `harness-v3-merged` | 6 | 4/6 | 12.8 | 4.7 | 10,499 |
| `harness-v6-guarded` | 3 | 2/3 | **7.3** | **2.0** | **5,756** |
| `harness-v5-lean` | 3 | 1/3 | 15.0 | 4.7 | 12,827 |
| `harness-v2-seeded` | 3 | 0/3 | 15.7 | 4.0 | 13,755 |
| `harness-v7-orchestrated` | 3 | 0/3 | 19.7 | 7.3 | 12,543 |

Raw evidence in `evals/results/runs/`; the ledger is in
[evals/CONFIGS.md](../evals/CONFIGS.md); the architecture is
[6.12](06-agent.md#612-an-alternative-architecture-the-ad-hoc-role-harness).

Four observations:

1. **The old 50% crash rate was two dead models, not the agent.** Groq 404s on
   `llama-4-scout` and `qwen3-32b`, and a 404 is not transient, so it killed the
   run. With both dropped from the eval pool the baseline goes 3/3. Every
   earlier number on this page was measuring that bug.
2. **The pass column is noise, demonstrably.** `harness-v3-merged` scored 3/3 in
   one batch and 1/3 in the next on an identical configuration, hours apart. Any
   ranking read off these rates would be invented — see
   [6.14.1](06-agent.md#6141-the-pass-column-is-noise-and-i-can-prove-it).
3. **The cost result is real and replicated.** 5,756 against 226,854 input
   tokens for the same task, stable across every rep and batch, with
   between-configuration spread far exceeding within-configuration variance.
4. **Every failure is `reasoning`** — 12 of 13, with zero `retrieval` and zero
   `tooling`. Every configuration found the file, edited it and ran the tests,
   then got the fix conceptually wrong. No change of topology can move that
   ([6.14.2](06-agent.md#6142-every-failure-is-reasoning-and-that-reframes-the-whole-exercise)).

## 11.4 Blockers

| Issue | Impact | State |
| --- | --- | --- |
| Only one scenario, at L0 | **The main gap.** Nothing can be compared on it: L0 is a canary, not a comparison instrument ([9.7](09-scenarios.md#97-the-difficulty-ladder)) | P4 |
| `404 model_not_found` still propagates and kills a run | Worked around for evals via `llm_router/config.eval.yaml`, not fixed. Any run on the default pool still dies on it | [13.2](13-roadmap.md#132-what-to-do-next) |
| `agent_evals` local history has diverged from its GitHub remote after the restructure | Scenarios aren't backed up | Needs a force-push decision |

## 11.5 What to do next

1. **Author scenarios** — L1 and L2, across the categories in
   [9.6](09-scenarios.md#96-categories-to-cover). Now the single blocking item:
   with one L0 task, no comparison can reach significance no matter how many
   reps it is given.
2. **Fix the dead-model crash properly.** The eval pool works around it; the
   shipping pool still dies on it. A decommissioned model should be disabled
   permanently, the way a rate-limited one is benched temporarily.
3. **Re-baseline at n=5** once scenarios exist, and record it in the ledger.
4. **P2, the judge** — worth building only once there are enough scenarios that
   reading diffs by hand hurts.

---

**Previous:** [← 10. Metrics](10-metrics.md) · **Next:** [12. Development harness →](12-development-harness.md)
