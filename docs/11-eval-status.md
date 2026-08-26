[← Wiki index](README.md)

# 11. Evaluation status

*The running state. This is the one page in the wiki expected to change often —
everything else describes design; this describes today.*

**Last updated: 2026-08-25.** Update it when a phase lands, a scenario is added,
or a comparison is decided. A status page nobody updates is worse than none.

## 11.1 One-paragraph summary

The pipeline works end to end, and there are now **five scenarios across four
topics**, three of them L1 or L2, including the set's first `trap`. What changed
this week is that there is now **one coding agent instead of two**: the
narrow-role graph was deleted and `agent/deep/` is what ships
([6.1.1](06-agent.md#611-the-arm-that-was-deleted)). It runs end to end but
**has never been run as an eval configuration**, so the comparison that would
have justified the choice never happened, and the cost gap it was losing on is
an open risk rather than a closed question; measuring it is now the top of
[13.2](13-roadmap.md#132-what-to-do-next). Pass rates
remain noise at affordable sample sizes, so the instrument still being invested
in is the *diagnostic* one.

## 11.2 What's built

| Phase | State | What it covers |
| --- | --- | --- |
| **P0** trace capture | **done** | `EVAL_TRACE_FILE` makes the agent append one JSON object per LLM/tool event ([trace.py](../agent/runtime/trace.py)). Every automatic metric is a sum over that file. The agent also fetches its LangSmith run tree and writes that down beside it ([7.6](07-observability.md#76-the-record-one-run-tree)). |
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

Raw evidence in `evals/results/runs/` — on the machine that ran it, not in
git; the ledger is in
[evals/CONFIGS.md](../evals/CONFIGS.md); the architectures are
[6.1](06-agent.md#61-one-conversation-on-the-pool).

### The deepagents arm, first look (2026-08-25)

**Not eval data.** Two ad-hoc runs on throwaway projects, outside the runner, no
scenario and no hidden tests — recorded because they bear on whether the
comparison is worth its quota, not because they measure anything.

| | task 1 | task 2 |
| --- | --- | --- |
| outcome | correct, minimal | correct, minimal |
| model calls | 21 | 14 |
| tool calls | 10 | 7 |
| `tokens_in` | 162,286 | 134,156 |

Three things worth carrying forward:

1. **The cost gap did not close.** Against the narrow-role harness's 5,756
   input tokens, this is 23–28×, and only ~30% under the conversational baseline
   that was deleted for exactly this. Different tasks, n=1 each, and short
   enough that summarization probably never triggered — but if the expectation
   was that the SDK's context management would erase a 39× gap, the first look
   says it does not.
2. **The project-context section pays for itself.** Task 2 added it and spent
   seven fewer model calls and three fewer tool calls, with no `glob` at all:
   the file tree arrived with the prompt instead of being discovered
   ([6.9.2](06-agent.md#652-the-configuration-ported-from-dcode)).
3. **No spec-gaming in either.** Both wrote the general fix rather than the one
   that satisfies the visible assertion, which is the failure
   [6.4.4](06-agent.md#644-closing-the-loop-is-not-the-same-as-being-right)
   records for the arm that has since been deleted. Two runs prove nothing; it
   is the first thing to check when this arm is finally priced.

**None of these configurations still exists.** They collapsed into one when the
harness was reduced to a single architecture; the rows stay because the
measurements are the project's data, and `git log` has the code each one names.

Four observations:

1. **The old 50% crash rate was two dead models, not the agent.** Groq 404s on
   `llama-4-scout` and `qwen3-32b`, and a 404 is not transient, so it killed the
   run. With both dropped from the eval pool the baseline goes 3/3. Every
   earlier number on this page was measuring that bug.
2. **The pass column is noise, demonstrably.** `harness-v3-merged` scored 3/3 in
   one batch and 1/3 in the next on an identical configuration, hours apart. Any
   ranking read off these rates would be invented — see
   [6.14.1](06-agent.md#642-the-pass-column-is-noise).
3. **The cost result is real and replicated.** 5,756 against 226,854 input
   tokens for the same task, stable across every rep and batch, with
   between-configuration spread far exceeding within-configuration variance.
4. **Every failure is `reasoning`** — 12 of 13, with zero `retrieval` and zero
   `tooling`. Every configuration found the file, edited it and ran the tests,
   then got the fix conceptually wrong. No change of topology can move that
   ([6.14.2](06-agent.md#643-every-failure-is-reasoning)).

## 11.4 Blockers

| Issue | Impact | State |
| --- | --- | --- |
| Five scenarios, none run more than n=2 | No longer *the* blocker, but nothing here has enough reps to compare configurations. `retry-after-case` (L0) is exhausted as an instrument ([9.7](09-scenarios.md#97-the-difficulty-ladder)) | P4 |
| Five of the eight scenario categories are still unwritten | `feature`, `tests`, `refactor`, `long-context` and `ambiguous` have never been run, so nothing probes size-based routing or multi-file construction ([9.6](09-scenarios.md#96-categories-to-cover)) | P4 |
| A model retired upstream costs an attempt on every run | No longer a crash: the router retires the member and carries on, which is what retired the trimmed eval pool. The retirement lasts one process, so each fresh run rediscovers it | [13.2](13-roadmap.md#132-what-to-do-next) |
| **Four of five scenario tags, and three of four topic branches, are local only** | `origin` has `topic/alerts` and a stale `scenario/retry-after-case`; everything else exists on one machine. Tags are not pushed by default, so this is silent. The scenario set is unrecoverable if the disk goes | [13.2](13-roadmap.md#132-what-to-do-next), item 2 |
| **The shipping agent has never run under the runner** | Its config exists (`evals/configs/deepagents.yaml`) and it works, but no eval run has been recorded — and the arm it replaced is deleted, so there is nothing left to compare it against except its own history | [13.2](13-roadmap.md#132-what-to-do-next), item 1 |

## 11.5 What to do next

1. **Run `deepagents` over the four non-exhausted scenarios** and establish
   what it actually costs. The interleaved comparison is no longer possible —
   the other arm is deleted — so the standard it is held to is its own recorded
   `input_tokens` against the 5,756 the deleted arm managed
   ([6.1.1](06-agent.md#611-the-arm-that-was-deleted)). Start at n=3 and check
   the quota arithmetic first: at ~20 calls a run, the flash tier funds about
   nine runs a day.
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
