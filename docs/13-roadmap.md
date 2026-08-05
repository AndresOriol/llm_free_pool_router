[← Wiki index](README.md)

# 13. Roadmap and scope

*The page for reasoning about where this repo goes next. Everything else in the
wiki describes what exists; this one is where scope gets argued and settled.*

## 13.1 The two phases of the project

**Phase 1 — the pool (current).** Make pooled free-tier capacity boring and
reliable enough that an agent can run unattended on it. The router *is* the
whole project right now.

**Phase 2 — the agent loop (direction only, not started).** Once the free pool
is solid, wire it as the model backend for
[deep agents](https://github.com/langchain-ai/deepagents) (LangGraph/LangSmith)
to get a real autonomous coding agent loop running on top of it.

The `agent/` package is a working *proof* that the pool serves a real agent
loop — it is not Phase 2. Phase 2 means investing in the loop itself, and it
doesn't start until told to.

## 13.2 What to do next

In order. The ordering is the argument.

1. **Fix the dead-model crash.** `404 model_not_found` propagates and kills a
   run ([4.6](04-failover.md#46-known-gaps)). Half of all measured runs died on
   it. Everything downstream of measurement is untrustworthy until this is
   fixed, which makes it the only thing with a claim to being first. A
   decommissioned model should be disabled *permanently*, the way a
   rate-limited one is benched *temporarily*.
2. **Author scenarios at L1 and L2.** With one L0 scenario there is nothing to
   compare configurations on ([11.4](11-eval-status.md#114-blockers)). This is
   the single biggest unblocker in the repo: without it, every proposed
   improvement is back to being decided by argument.
3. **Re-baseline at n=5**, and record it in the ledger.
4. **Then** the judge (P2), then compare/report (P3).

### Evaluation build order

| Phase | State | Scope |
| --- | --- | --- |
| P0 — trace capture | done | `EVAL_TRACE_FILE`. The *measuring instrument*, so it had to land on `master` before any baseline. |
| P1 — runner | done | materialize → run → verify → integrity → record, plus `validate`. Automatic metrics including the failure taxonomy. |
| P2 — judge | next after scenarios | `claude -p`, pinned rubric, diff-hash cache. |
| P3 — compare/report | after P2 | Leaderboard, written comparisons, interleaved execution reporting. |
| P4 — scenario library | **in progress, 1 of ~15** | L0–L2 across the category list; `langwatch/scenario` adapter for the ambiguous category. |
| P5 — L3 | last | SWE-bench Lite behind Docker, as an absolute-progress marker. Optionally the OpenAI-compatible router shim if driving external harnesses is ever wanted. |
| Later | — | A free-pool judge, validated for agreement against the Claude judge on a labelled set before it replaces it. |

## 13.3 Known constraints that shape the roadmap

These are facts about the environment, not problems to solve by cleverness. Any
plan that ignores one of them is wrong.

- **Groq's free tier is one shared request budget per account**, not one per
  model. Adding more Groq *models* buys almost nothing; adding more Groq
  *accounts* buys real capacity ([3.4](03-pool-model.md#34-priority-tiers)).
- **Gemini's shape is the mirror image**: huge token budgets, very few requests
  per day. The pool only works because the two shapes complement each other.
- **A one-line fix costs ~10 provider calls across 5–7 models.** Throughput, not
  intelligence, is the binding constraint on long tasks.
- **Cooldown state is per process.** Two concurrent agents on the same keys each
  rediscover which accounts are hot.
- **A full comparison costs a meaningful fraction of a day's quota**
  ([8.9](08-evaluation-method.md#89-budget)). Evaluation competes with the
  actual work for the same free tier.

## 13.4 Open questions

Live, unresolved, and worth deciding when the evidence arrives — not before.

| Question | What would settle it |
| --- | --- |
| Default repetition count: 3 is cheap but weak, 5 costs most of a day's quota on a full suite. | The first real baseline's observed run-to-run variance. |
| Should **pinned-model mode** be the default comparison mode, with pool mode reserved for final validation? | Whether pool-mode variance actually swamps the effect sizes we care about. |
| Does the agent get a git tool? | If yes, scenarios can ship real history ("find the commit that broke this") and materialization becomes a bundle restore instead of `git archive`. |
| How does a free-pool judge get validated? | It must agree with the Claude judge on a labelled set before its scores can be trusted. |
| Where does shared cooldown state live, if it ever needs to be shared across processes? | A second concurrent consumer actually existing. |
| Where do per-provider curated docs live once the provider list grows? | The provider list growing past what one page holds ([5. Providers](05-providers.md)). |
| Should deepagents' summarization be tuned for the pool's real (much smaller) context windows? | It's a candidate change like any other — measure it. Proposed as S5 in [6.9](06-agent.md#69-proposed-strategies). |
| Should the agent keep the `task`/subagent tool at all? It costs 31% of the per-step budget and is never configured. | An L2 multi-file scenario run with and without it. See the tension in [6.9.1](06-agent.md#691-the-one-real-tension). |

## 13.5 Settled decisions

Don't re-litigate these without new evidence. Each was chosen for a reason that
still holds.

| Decision | Why |
| --- | --- |
| Priority is a hand-assigned number; no dynamic scoring. | Cooldown already reacts to real-time availability. Scoring adds tuning surface with no evidence it's needed. |
| The router selects; it never generates. | Giving it a `.generate()` would force `llm_router` to import `agent` and invert the layering. |
| `RouterChatModel` subclasses `BaseChatModel` rather than being a bespoke client. | Provider SDKs already handle message formatting, tool parsing and streaming; and the pool becomes usable anywhere in the LangChain ecosystem. |
| Non-transient errors are raised, never rerouted. | Silently rerouting a malformed-request bug burns the pool and hides the bug behind "all providers exhausted". |
| Provider SDKs run with `max_retries=0`. | The SDK would retry the same dead account — the job the router owns one level up. |
| Scenarios live in a separate repo from the harness. | Configurations are branches of this repo; anything shared and append-only would conflict on every merge. |
| Results are one directory per run, with no index. | Same reason. A summary is a glob. |
| LangSmith is for watching, never for the record. | Hosted traces expire; a verdict must rest on files on disk. |
| Only the sync path is implemented. | Both callers are sync. A hand-written async loop was built, found unused, and removed. |
| Docker is optional until L3. | L0–L2 scenarios are authored dependency-free, so the restricted `python`/`pytest` backend suffices. |

## 13.6 Explicitly out of scope

Restating [1.4](01-overview.md#14-what-is-deliberately-not-built), because scope
creep here would be expensive: no general-purpose gateway (auth, multi-tenancy,
billing, admin UI), no paid-tier fallback, no ToS circumvention.

## 13.7 How to propose a change

1. Check it against the north star. If it doesn't serve *"keep the free token
   pool available for an unattended agent"*, it doesn't get built — regardless
   of how good an idea it is in the abstract.
2. Check it against [13.5](#135-settled-decisions) and
   [13.6](#136-explicitly-out-of-scope). If it reopens one, say what new
   evidence justifies that.
3. Make it a branch, add a configuration in [evals/configs/](../evals/configs/),
   and measure it against baseline on the suite that covers it
   ([8. Evaluation method](08-evaluation-method.md)).
4. Record the verdict in [evals/CONFIGS.md](../evals/CONFIGS.md) — win or lose.
5. Merge or drop. A change that can't be shown to help doesn't merge, and a draw
   keeps the simpler configuration.

---

**Previous:** [← 12. Development harness](12-development-harness.md) · **Back to** [wiki index](README.md)
