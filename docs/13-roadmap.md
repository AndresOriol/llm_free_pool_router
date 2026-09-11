[← Wiki index](README.md)

# 13. Roadmap and scope

*The page for reasoning about where this repo goes next. Everything else in the
wiki describes what exists; this one is where scope gets argued and settled.*

## 13.1 The two phases of the project

**Phase 1 — the pool (done enough).** Make pooled free-tier capacity boring and
reliable enough that an agent can run unattended on it. Failover, size-aware
selection, cooldown, retirement and the quota panel are all in place.

**Phase 2 — the agent loop (in progress).** A standing maintainer: a session
that works one project unattended on its own branch, updates its docs, and
writes an account a human reviews instead of the code
([design note](design/long-run-harness.md)).

The question of **which harness** is closed by decision rather than by
measurement: the narrow-role arm is deleted and `agent/code/` is what ships
([6.1.1](06-agent.md#611-the-arm-that-was-deleted)). What that bets is that a
maintained SDK harness plus a 128,000-token floor does the job well enough that
maintaining a bespoke one is not worth it — and the bet is **unsettled**, because
the deleted arm was winning on cost when it was retired. The open item is no
longer *which*, it is *what the survivor costs*.

**The redirection that made this possible** was to stop treating Groq's
8,000-token ceiling as a constraint on *coding* work. A Groq account holds
100,000 tokens per day, so one wide request would spend the whole day's budget —
those members can never serve a conversation. A coding session therefore
declares a hard floor and routes only above it
([4.2](04-failover.md#42-size-aware-selection)), and Groq stays in the pool for
work that fits it.

## 13.2 What to do next

In order. The ordering is the argument.

1. **Price the surviving arm.** `code`, n=3 to start, over the four
   scenarios that still discriminate. There is no longer another arm to
   interleave against, so the standard is the deleted one's recorded 5,756 input
   tokens. Until this lands, the harness choice rests on argument, which is the
   thing this repo exists not to do. Budget it first: a run cost 14–21 model
   calls in the spike, and the flash tier is 20 requests per day per model per
   account ([8.9](08-evaluation-method.md#89-budget)).
2. **Rebuild the standing-maintainer loop on this arm.** `NOTES.md` in, an
   account out, a journal that survives a crash, and incremental commits all
   lived in the deleted arm's `record/`. The coding agent commits and does none
   of the rest, so Phase 2's daily cycle has a hole in it
   ([design note](design/long-run-harness.md)).
3. **Back up `agent_evals`.** Four of five scenario tags and three of four topic
   branches exist only on the machine that made them. Tags are never pushed by
   default, so this is one command and it is the only item here that loses data
   if left ([11.4](11-eval-status.md#114-blockers)).
4. **Keep authoring scenarios.** `long-context` next: size-based routing is half
   the architecture and nothing probes it.
5. **Make the dead-model retirement outlive the process.** `404
   model_not_found` no longer kills a run — the router retires the member and
   carries on ([4.6](04-failover.md#46-known-gaps)), which is what let the
   trimmed eval pool go. But `retire()` marks one provider instance, so every
   fresh run spends an attempt rediscovering the same dead model. A
   decommissioned model should be disabled *permanently*, the way a
   rate-limited one is benched *temporarily*.
6. **Prune the run tree.** 79% of a recorded tree was middleware wrapper spans
   carrying nothing ([7.6](07-observability.md#76-the-record-one-run-tree)).
   Worth doing once a metric actually reads the tree, not before.
7. **Then** the judge (P2), then compare/report (P3).

### 13.2.1 What the architecture work settled, and what it did not

`harness/adhoc-router` holds the narrow-role arm as it was when it was deleted
([6.1.1](06-agent.md#611-the-arm-that-was-deleted)). Seven variants and a
conversational baseline were measured against it; all of them are in `git log`
now. What that measurement established, and what a fresh session
should not redo:

- **Cost replicated: 7.3 calls and 5,756 input tokens against the conversational
  loop's 20.0 and 226,854.** A 39× reduction, stable across every rep and batch.
  This is why the architecture is what it is.
- **Pass rates on L0 are noise.** Do not re-run configurations against
  `retry-after-case`; the answer will be a different random ordering
  ([6.8.2](06-agent.md#642-the-pass-column-is-noise)).
- **Every failure was `reasoning`** — 12 of 13, zero `retrieval`, zero
  `tooling`. Each configuration found the file, edited it, ran the tests, and
  got the fix conceptually wrong
  ([6.8.3](06-agent.md#643-every-failure-is-reasoning)).
  **This is the load-bearing result**: topology changes address retrieval,
  tooling and stopping, and none of those is the bottleneck. Further
  architecture work has close to nothing left to give on correctness.

So the architecture is a *cost* win whose *correctness* is still unpriced. What
prices it is a scenario set and a diagnosis of what actually goes wrong inside a
run, not more topologies.

**None of this argues against the arm that survived.** Everything above says
that rearranging *this repo's* roles cannot buy correctness. The question the
surviving arm answers is a different one — whether a harness this repo does not
maintain does the same job well enough that the maintenance is not worth paying
for — and it is a cost-of-ownership question with a correctness floor. It was
answered by decision; the measurement that would have checked it is item 1
above.

### Evaluation build order

| Phase | State | Scope |
| --- | --- | --- |
| P0 — trace capture | done | `EVAL_TRACE_FILE`. The *measuring instrument*, so it had to land on `master` before any baseline. |
| P1 — runner | done | materialize → run → verify → integrity → record, plus `validate`. Automatic metrics including the failure taxonomy. |
| P2 — judge | next after scenarios | `claude -p`, pinned rubric, diff-hash cache. |
| P3 — compare/report | after P2 | Leaderboard, written comparisons, interleaved execution reporting. |
| P4 — scenario library | **in progress, 5 of ~15** | L0–L2 across the category list; `langwatch/scenario` adapter for the ambiguous category. |
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
- **A conversation costs one to two orders of magnitude more than narrow roles.**
  A one-line fix ran to ~20 calls and ~227,000 input tokens on the original
  conversational loop, and 14–21 calls and 134,000–162,000 tokens on the current
  coding agent, against 7.3 and 5,756 for narrow roles. Throughput is the
  binding constraint on long tasks — though on the scenarios measured so far,
  *correctness* failures were all `reasoning`, so throughput and capability are
  separate problems and only the first has been solved.
- **A wide-context session is request-bound, not token-bound.** At ~20 calls a
  run and 20 requests per day per flash model per account, the flash tier funds
  roughly nine runs a day; the lite and Gemma tiers are what make a real batch
  affordable ([5.4](05-providers.md#54-current-free-tier-limits)).
- **Two Groq models are decommissioned** (`llama-4-scout`, `qwen3-32b`) and 404.
  The router retires each one on the attempt that discovers it and carries on,
  but only for that process. A dead model is only reached if a request is small
  enough to pass the size filter, so cheaper agents pay that attempt on every
  run and token-heavy ones never do — audit the pool before reading any
  efficiency result.
- **Cooldown state is per process.** Two concurrent agents on the same keys each
  rediscover which accounts are hot.
- **A full comparison costs a meaningful fraction of a day's quota**
  ([8.9](08-evaluation-method.md#89-budget)). Evaluation competes with the
  actual work for the same free tier.

## 13.4 Open questions

Live, unresolved, and worth deciding when the evidence arrives — not before.

| Question | What would settle it |
| --- | --- |
| **Was deleting the narrow-role arm a mistake?** | `tokens_in` per run for `code` across the discriminating scenarios, read against the deleted arm's recorded 5,756 ([13.2](#132-what-to-do-next), item 1). |
| Does the coding agent need the parts of `deepagents-code` not carried over — skills, memory, the rubric grader? | A failure the comparison produces that one of them would have prevented. Not before ([6.9.2](06-agent.md#652-the-configuration-ported-from-dcode)). |
| Which run-tree fields are worth keeping? | A metric actually reading the tree. 79% of the bytes are middleware wrappers, but "unused today" is not "surplus" ([7.6](07-observability.md#76-the-record-one-run-tree)). |
| Default repetition count: 3 is cheap but weak, 5 costs most of a day's quota on a full suite. | The first real baseline's observed run-to-run variance. |
| Should **pinned-model mode** be the default comparison mode, with pool mode reserved for final validation? | Whether pool-mode variance actually swamps the effect sizes we care about. |
| Does the agent get a git tool? | If yes, scenarios can ship real history ("find the commit that broke this") and materialization becomes a bundle restore instead of `git archive`. |
| How does a free-pool judge get validated? | It must agree with the Claude judge on a labelled set before its scores can be trusted. |
| Where does shared cooldown state live, if it ever needs to be shared across processes? | A second concurrent consumer actually existing. |
| Where do per-provider curated docs live once the provider list grows? | The provider list growing past what one page holds ([5. Providers](05-providers.md)). |
| Should the web explorer's notes be read by the coding agent automatically, or only when the brief says to? | A run where the coding agent ignored a `/research/` file that answered its question ([15.1](15-explorer.md#151-what-it-is-for)). |
| **Does a coding agent that *can* delegate delegate when it should?** | A `code-peers` batch against the non-delegating baseline: how many `python -m agent.explore` calls each run made, and whether the runs that made one were the ones needing outside knowledge ([16.6](16-delegation.md#166-what-this-costs-and-what-is-unmeasured)). |
| What should a delegation's budget be? | The first observed distribution of `tokens_in` for a delegated task. Only the wall clock is bounded today (`AGENT_DELEGATE_TIMEOUT`); the token number is not invented before then ([16.5](16-delegation.md#165-what-a-delegation-costs)). |

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
| ~~LangSmith is for watching, never for the record.~~ **Reopened.** The record is now the run tree LangSmith already built, *snapshotted* to disk after the run. | The requirement — a verdict rests on files on disk — is met by the snapshot. What expiry forbids is depending on the hosted copy at scoring time, not asking for the tree once while it exists ([7.6](07-observability.md#76-the-record-one-run-tree)). |
| ~~One architecture; the conversational loop is deleted, not disabled.~~ **Reopened.** | Its inputs changed: the SDK now ships summarization and offloading, and the floor for coding work is 128,000 tokens rather than 6,000. The 39× result still stands, which is why the new arm is a configuration and not a merge ([6.1](06-agent.md#61-one-conversation-on-the-pool)). |
| Only the sync path is implemented. | Both callers are sync. A hand-written async loop was built, found unused, and removed. |
| ~~The two agents meet on disk and nowhere else; no message bus, no protocol.~~ **Partly reopened.** The coding agent can ask the explorer for a report; the *deliverable* is still a file. | A human was the message bus, which does not survive an unattended run or a third agent. The request is a command line — the agent runs `python -m agent.explore` with `execute` — so there is still no protocol to keep in step ([16](16-delegation.md)). |
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

**Previous:** [← 12. Development harness](12-development-harness.md) · **Next:** [14. Quota panel →](14-quota-panel.md)
