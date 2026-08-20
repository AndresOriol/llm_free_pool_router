[← Wiki index](README.md)

# 11. Evaluation status

*The running state. This is the one page in the wiki expected to change often —
everything else describes design; this describes today.*

**Last updated: 2026-08-20.** Update it when a phase lands, a scenario is added,
or a comparison is decided. A status page nobody updates is worse than none.

## 11.1 One-paragraph summary

The pipeline works end to end. The measurement is no longer bottlenecked on
having a single L0 scenario: there are now **five scenarios across four
topics**, three of them L1 or L2, including the set's first `trap`. Pass rates
are still noise at the sample sizes affordable here, so the instrument being
built out is the *diagnostic* one — a per-turn transcript of what every role was
given, and a per-run post-mortem that reads it
([design note §9](design/long-run-harness.md#9-reading-one-session-back-the-post-mortem)).
The next move is a batch at n=5 over the new scenarios, with a second Gemini
account making that affordable.

## 11.2 What's built

| Phase | State | What it covers |
| --- | --- | --- |
| **P0** trace capture | **done** | `EVAL_TRACE_FILE` makes the agent append one JSON object per LLM/tool event ([trace.py](../agent/trace.py)). Every automatic metric is a sum over that file. A session also writes a per-turn transcript of what each role was handed ([7.6](07-observability.md#76-what-a-session-records-about-itself)). |
| **P1** runner | **done** | [evals/](../evals/): materialize → run → verify → integrity → record, plus `validate` and `show`. Metrics automatic, including the failure taxonomy. |
| **P2** judge | not started | `claude -p` with a pinned rubric and diff-hash cache. Quality scoring is manual until then. |
| **P3** compare/report | not started | Leaderboard and written comparisons. `show` covers the basics today. |
| **P4** scenario library | **5 of ~15** | Four topics. `retry-after-case` (L0, exhausted), `duration-notes` (L1), `threshold-off-by-one` (L2), `stale-categories` (L1, symptom-only), `count-and-share` (L2, trap). Still the main gap, but no longer a blocker. |
| **P5** SWE-bench L3 | not started | Needs Docker. Deliberately last. |

## 11.3 Where the numbers stand

**All of this is `retry-after-case` (L0) only**, n=3, interleaved on an
identical pool. It predates the four scenarios added since, and no
configuration has been run against those more than n=2.

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
| Five scenarios, none run more than n=2 | No longer *the* blocker, but nothing here has enough reps to compare configurations. `retry-after-case` (L0) is exhausted as an instrument ([9.7](09-scenarios.md#97-the-difficulty-ladder)) | P4 |
| Five of the eight scenario categories are still unwritten | `feature`, `tests`, `refactor`, `long-context` and `ambiguous` have never been run, so nothing probes size-based routing or multi-file construction ([9.6](09-scenarios.md#96-categories-to-cover)) | P4 |
| `404 model_not_found` still propagates and kills a run | Worked around for evals via `llm_router/config.eval.yaml`, not fixed. Any run on the default pool still dies on it | [13.2](13-roadmap.md#132-what-to-do-next) |
| `agent_evals` local history has diverged from its GitHub remote after the restructure | Scenarios aren't backed up | Needs a force-push decision |

## 11.5 What to do next

1. **Run a batch at n=5** over the four non-exhausted scenarios. The second
   Gemini account roughly doubles the affordable reps, and enough *varied
   failures to read* is what the diagnostic loop is short of — not more
   architecture.
2. **Post-mortem every run in it**, then J2 over the batch. The per-run reviewer
   is built and has never been run against a real session.
3. **Keep authoring scenarios** — `long-context` next, since size-based routing
   is half the architecture and nothing probes it.
4. **Fix the dead-model crash properly.** The eval pool works around it; the
   shipping pool still dies on it. A decommissioned model should be disabled
   permanently, the way a rate-limited one is benched temporarily.
5. **P2, the judge** — worth building only once there are enough scenarios that
   reading diffs by hand hurts.

---

**Previous:** [← 10. Metrics](10-metrics.md) · **Next:** [12. Development harness →](12-development-harness.md)
