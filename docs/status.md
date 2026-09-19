[← Wiki index](README.md)

# Status and roadmap

*The running state. This is the one page in the wiki expected to change often —
everything else describes design; this describes today.*

**Last updated: 2026-09-05.** Update it when a phase lands, a scenario is added,
or a comparison is decided. A status page nobody updates is worse than none.

## Where things stand right now

*The three facts most likely to mislead someone picking this up cold. Everything
else on this page is design and changes rarely; this block is state.*

- **There is one coding agent now, and its cost is unproven.** The narrow-role
  arm is deleted; `agent/code/` keeps a conversation on the pool's *widest*
  members (≥128,000 input tokens), configured the way `deepagents-code`
  configures one. That was a decision about what to maintain, **not** a finding:
  the last measurement had the deleted arm winning on input tokens 39× over, on
  inputs that no longer hold and that nobody has re-run
  ([The arm that was deleted](agents/code.md#the-arm-that-was-deleted)). Watch `tokens_in`.
- **A second agent researches the web.** `agent/explore/` shares the loop, the
  pool and the jail, swaps the shell for `tavily_search`/`think_tool`, and hands
  off to the coding agent by writing files ([The web explorer](agents/explore.md)). It runs
  LangChain's deep-research workflow rather than a method written here, and its
  search returns the **page** rather than a summary of it — which is the fix for
  a run that searched 13 times and opened nothing
  ([The deep-research port](agents/explore.md#the-deep-research-port)). Needs a Tavily key; the
  pool holds one.
- **The coding agent can now ask the explorer for research mid-task**, by
  running `python -m agent.explore` with `execute` — no protocol, no tool
  ([Delegation](agents/delegation.md)). It is on by default and a delegation costs a whole
  explorer session, so a run's `tokens_in` may include one; the record carries
  `peers` and `AGENT_PEERS=` turns it off. **Unmeasured** — `code-peers` has never been run
  against `code`.
- **A third agent now reads the other two's runs and has the coding agent fix
  what recurs** ([The improvement agent](agents/improve.md)). It cannot edit a file — the fix
  is always delegated — and an issue in `evals/results/issues/` closes only when
  runs recorded *after* the fix stop matching its signature. **Unmeasured.** Its
  first live pass went round the whole loop and diagnosed a failure that had
  already been fixed, then relayed a coding agent's claim to have made a change
  the diff did not contain — read
  [What it costs, and what is unmeasured](agents/improve.md#what-it-costs-and-what-is-unmeasured) before
  trusting a pass. `IMPROVE_FIX=0` leaves a diagnose-only arm.
- **The explorer has been watched once, and it drifted.** A live run searched 13 times, opened **zero** sources, and wrote one file at the end — all against its own prompt, and none of it visible to any existing metric. The prompt now carries numeric rules and
  [evals/research_trajectory.py](../evals/research_trajectory.py) checks them ([Measured against a reference research agent](agents/explore.md#measured-against-a-reference-research-agent),
  [What the first live delegation showed](agents/delegation.md#what-the-first-live-delegation-showed)).
- **The one L0 scenario is exhausted as a measuring instrument.** Seven
  configurations were run against it; none could be distinguished from another,
  and one scored 3/3 and 1/3 on consecutive batches. Re-running them will
  produce a different random ordering, not an answer
  ([The pass column is noise](agents/code.md#the-pass-column-is-noise)).
  There are now four more scenarios, at L1 and L2, and none has been run past
  n=2 ([What's built](status.md#whats-built)).
- **A run's record is one object plus one flat file.** The agent fetches a
  single LangSmith run tree and writes it down; the `EVAL_TRACE_FILE` JSONL is
  still written alongside it, because every automatic metric is summed over that
  and it has to exist when LangSmith does not
  ([The record: one run tree](evaluation/observability.md#the-record-one-run-tree)). This reverses
  "LangSmith is for watching, never for the record" — the expiry argument is
  answered by snapshotting the tree, not by rebuilding it by hand
  ([Settled decisions](status.md#settled-decisions)).

## One-paragraph summary

The pipeline works end to end, and there are now **five scenarios across four
topics**, three of them L1 or L2, including the set's first `trap`. What changed
this week is that there is now **one coding agent instead of two**: the
narrow-role graph was deleted and `agent/code/` is what ships
([The arm that was deleted](agents/code.md#the-arm-that-was-deleted)). It runs end to end but
**has never been run as an eval configuration**, so the comparison that would
have justified the choice never happened, and the cost gap it was losing on is
an open risk rather than a closed question; measuring it is now the top of
[What to do next](#what-to-do-next-1). Pass rates
remain noise at affordable sample sizes, so the instrument still being invested
in is the *diagnostic* one.

## What's built

| Phase | State | What it covers |
| --- | --- | --- |
| **P0** trace capture | **done** | `EVAL_TRACE_FILE` makes the agent append one JSON object per LLM/tool event ([trace.py](../agent/utils/trace.py)). Every automatic metric is a sum over that file. The agent also fetches its LangSmith run tree and writes that down beside it ([The record: one run tree](evaluation/observability.md#the-record-one-run-tree)). |
| **P1** runner | **done** | [evals/](../evals/): materialize → run → verify → integrity → record, plus `validate` and `show`. Metrics automatic, including the failure taxonomy. |
| **P2** judge | not started | `claude -p` with a pinned rubric and diff-hash cache. Quality scoring is manual until then. |
| **P3** compare/report | not started | Leaderboard and written comparisons. `show` covers the basics today. |
| **P4** scenario library | **5 of ~15** | Four topics. `retry-after-case` (L0, exhausted), `duration-notes` (L1), `threshold-off-by-one` (L2), `stale-categories` (L1, symptom-only), `count-and-share` (L2, trap). Still the main gap, but no longer a blocker. |
| **P5** SWE-bench L3 | not started | Needs Docker. Deliberately last. |

## Where the numbers stand

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
[One conversation, on the pool](agents/code.md#one-conversation-on-the-pool).

### The coding agent, first look (2026-08-25)

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
   ([The configuration, ported from dcode](agents/code.md#the-configuration-ported-from-dcode)).
3. **No spec-gaming in either.** Both wrote the general fix rather than the one
   that satisfies the visible assertion, which is the failure
   [Closing the loop is not the same as being right](agents/code.md#closing-the-loop-is-not-the-same-as-being-right)
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
   [The pass column is noise](agents/code.md#the-pass-column-is-noise). It was also, separately,
   *wrong*: it counted an integrity verdict as a failed task, which cost
   `context-and-gate` two runs and `deepagents` three
   ([Why the ledger counts `verified` and not `outcome`](evaluation/metrics.md#why-the-ledger-counts-verified-and-not-outcome)).
3. **The cost result is real and replicated.** 5,756 against 226,854 input
   tokens for the same task, stable across every rep and batch, with
   between-configuration spread far exceeding within-configuration variance.
4. **Every failure is `reasoning`** — 12 of 13, with zero `retrieval` and zero
   `tooling`. Every configuration found the file, edited it and ran the tests,
   then got the fix conceptually wrong. No change of topology can move that
   ([Every failure is `reasoning`](agents/code.md#every-failure-is-reasoning)).

## Blockers

| Issue | Impact | State |
| --- | --- | --- |
| Five scenarios, none run more than n=2 | No longer *the* blocker, but nothing here has enough reps to compare configurations. `retry-after-case` (L0) is exhausted as an instrument ([The difficulty ladder](evaluation/scenarios.md#the-difficulty-ladder)) | P4 |
| The set is a repair set | Every scenario is a broken seed plus a hidden test pinning the fix. Generative requests — "build X" — are probed by nothing, and the empty-patch gate degenerates on them, so the oracle needs a second reference implementation before one can be authored ([design note](design/generative-scenarios.md)) | P4 |
| Five of the nine scenario categories are still unwritten | `generative`, `tests`, `refactor`, `long-context` and `ambiguous`, so nothing probes size-based routing or construction. Don't maintain this list by hand — the catalogue's **Gaps** section derives it from the tags ([The catalogue](evaluation/scenarios.md#the-catalogue)); this row was already wrong before it did, naming `feature`, which `duration-notes` has covered since August | P4 |
| A model retired upstream costs an attempt on every run | No longer a crash: the router retires the member and carries on, which is what retired the trimmed eval pool. The retirement lasts one process, so each fresh run rediscovers it | [What to do next](#what-to-do-next-1) |
| **Four of five scenario tags, and three of four topic branches, are local only** | `origin` has `topic/alerts` and a stale `scenario/retry-after-case`; everything else exists on one machine. Tags are not pushed by default, so this is silent. The scenario set is unrecoverable if the disk goes | [What to do next](#what-to-do-next-1), item 2 |
| **The shipping agent has never run under the runner, and could not have been** | `evals/configs/code.yaml` pinned `ref: harness/deepagents`, a branch merged into `master` and deleted locally. `agent_config.load` resolves `ref` to a SHA at load time and **raises** when it cannot, so `--config code` never started; `code-peers` was broken the same way. Fixed on `harness/eval-generative` — both now pin `master`. This is why item 1 of [What to do next](#what-to-do-next-1) never happened, and it was invisible because nothing runs the configs | fixed; the runs are still owed |
| **`bad_tool_calls` reads zero on every run ever recorded** | Not a result. Soft tool errors are detected by `str(output).startswith("Error")`, while the tools return lowercase `error:` and deepagents returns a `ToolMessage` whose repr starts `content=`. Measured: 0 hits against 94 error-shaped outputs over 1,093 `tool_end` events. `trace.py` hardcodes `ok=True`, discarding the status the tool already sets | [design note](design/generative-scenarios.md) §6 |

## What to do next

1. **Run `code` over the four non-exhausted scenarios** and establish
   what it actually costs. The interleaved comparison is no longer possible —
   the other arm is deleted — so the standard it is held to is its own recorded
   `tokens_in` against the 5,756 the deleted arm managed
   ([The arm that was deleted](agents/code.md#the-arm-that-was-deleted)). Start at n=3 and check
   the quota arithmetic first: at ~20 calls a run, the flash tier funds about
   nine runs a day.
2. **Post-mortem every run in it**, then J2 over the batch. The per-run reviewer
   is built and has never been run against a real session.
3. **Keep authoring scenarios** — `long-context` next, since size-based routing
   is half the architecture and nothing probes it.
4. **Make the dead-model retirement outlive the process.** It no longer crashes
   a run, but `retire()` marks one provider instance, so every fresh run spends
   an attempt rediscovering the same dead model. A decommissioned model should
   be disabled permanently, the way a rate-limited one is benched temporarily.
5. **P2, the judge** — worth building only once there are enough scenarios that
   reading diffs by hand hurts.

---



## Roadmap and scope

*The page for reasoning about where this repo goes next. Everything else in the
wiki describes what exists; this one is where scope gets argued and settled.*

### The two phases of the project

**Phase 1 — the pool (done enough).** Make pooled free-tier capacity boring and
reliable enough that an agent can run unattended on it. Failover, size-aware
selection, cooldown, retirement and the quota panel are all in place.

**Phase 2 — the agent loop (in progress).** A standing maintainer: a session
that works one project unattended on its own branch, updates its docs, and
writes an account a human reviews instead of the code
([design note](design/long-run-harness.md)).

The question of **which harness** is closed by decision rather than by
measurement: the narrow-role arm is deleted and `agent/code/` is what ships
([The arm that was deleted](agents/code.md#the-arm-that-was-deleted)). What that bets is that a
maintained SDK harness plus a 128,000-token floor does the job well enough that
maintaining a bespoke one is not worth it — and the bet is **unsettled**, because
the deleted arm was winning on cost when it was retired. The open item is no
longer *which*, it is *what the survivor costs*.

**The redirection that made this possible** was to stop treating Groq's
8,000-token ceiling as a constraint on *coding* work. A Groq account holds
100,000 tokens per day, so one wide request would spend the whole day's budget —
those members can never serve a conversation. A coding session therefore
declares a hard floor and routes only above it
([Size-aware selection](pool/failover.md#size-aware-selection)), and Groq stays in the pool for
work that fits it.

### What to do next

In order. The ordering is the argument.

1. **Price the surviving arm.** `code`, n=3 to start, over the four
   scenarios that still discriminate. There is no longer another arm to
   interleave against, so the standard is the deleted one's recorded 5,756 input
   tokens. Until this lands, the harness choice rests on argument, which is the
   thing this repo exists not to do. Budget it first: a run cost 14–21 model
   calls in the spike, and the flash tier is 20 requests per day per model per
   account ([Budget](evaluation/method.md#budget)).
2. **Rebuild the standing-maintainer loop on this arm.** `NOTES.md` in, an
   account out, a journal that survives a crash, and incremental commits all
   lived in the deleted arm's `record/`. The coding agent commits and does none
   of the rest, so Phase 2's daily cycle has a hole in it
   ([design note](design/long-run-harness.md)).
3. **Back up `agent_evals`.** Four of five scenario tags and three of four topic
   branches exist only on the machine that made them. Tags are never pushed by
   default, so this is one command and it is the only item here that loses data
   if left ([Blockers](#blockers)).
4. **Keep authoring scenarios.** `long-context` next: size-based routing is half
   the architecture and nothing probes it.
5. **Make the dead-model retirement outlive the process.** `404
   model_not_found` no longer kills a run — the router retires the member and
   carries on ([Known gaps](pool/failover.md#known-gaps)), which is what let the
   trimmed eval pool go. But `retire()` marks one provider instance, so every
   fresh run spends an attempt rediscovering the same dead model. A
   decommissioned model should be disabled *permanently*, the way a
   rate-limited one is benched *temporarily*.
6. **Prune the run tree.** 79% of a recorded tree was middleware wrapper spans
   carrying nothing ([The record: one run tree](evaluation/observability.md#the-record-one-run-tree)).
   Worth doing once a metric actually reads the tree, not before.
7. **Then** the judge (P2), then compare/report (P3).

#### What the architecture work settled, and what it did not

`harness/adhoc-router` holds the narrow-role arm as it was when it was deleted
([The arm that was deleted](agents/code.md#the-arm-that-was-deleted)). Seven variants and a
conversational baseline were measured against it; all of them are in `git log`
now. What that measurement established, and what a fresh session
should not redo:

- **Cost replicated: 7.3 calls and 5,756 input tokens against the conversational
  loop's 20.0 and 226,854.** A 39× reduction, stable across every rep and batch.
  This is why the architecture is what it is.
- **Pass rates on L0 are noise.** Do not re-run configurations against
  `retry-after-case`; the answer will be a different random ordering
  ([The pass column is noise](agents/code.md#the-pass-column-is-noise)).
- **Every failure was `reasoning`** — 12 of 13, zero `retrieval`, zero
  `tooling`. Each configuration found the file, edited it, ran the tests, and
  got the fix conceptually wrong
  ([Every failure is `reasoning`](agents/code.md#every-failure-is-reasoning)).
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

#### Evaluation build order

| Phase | State | Scope |
| --- | --- | --- |
| P0 — trace capture | done | `EVAL_TRACE_FILE`. The *measuring instrument*, so it had to land on `master` before any baseline. |
| P1 — runner | done | materialize → run → verify → integrity → record, plus `validate`. Automatic metrics including the failure taxonomy. |
| P2 — judge | next after scenarios | `claude -p`, pinned rubric, diff-hash cache. |
| P3 — compare/report | after P2 | Leaderboard, written comparisons, interleaved execution reporting. |
| P4 — scenario library | **in progress, 5 of ~15** | L0–L2 across the category list; `langwatch/scenario` adapter for the ambiguous category. |
| P5 — L3 | last | SWE-bench Lite behind Docker, as an absolute-progress marker. Optionally the OpenAI-compatible router shim if driving external harnesses is ever wanted. |
| Later | — | A free-pool judge, validated for agreement against the Claude judge on a labelled set before it replaces it. |

### Known constraints that shape the roadmap

These are facts about the environment, not problems to solve by cleverness. Any
plan that ignores one of them is wrong.

- **Groq's free tier is one shared request budget per account**, not one per
  model. Adding more Groq *models* buys almost nothing; adding more Groq
  *accounts* buys real capacity ([Priority tiers](pool/model.md#priority-tiers)).
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
  affordable ([Current free-tier limits](pool/providers.md#current-free-tier-limits)).
- **Two Groq models are decommissioned** (`llama-4-scout`, `qwen3-32b`) and 404.
  The router retires each one on the attempt that discovers it and carries on,
  but only for that process. A dead model is only reached if a request is small
  enough to pass the size filter, so cheaper agents pay that attempt on every
  run and token-heavy ones never do — audit the pool before reading any
  efficiency result.
- **Cooldown state is per process.** Two concurrent agents on the same keys each
  rediscover which accounts are hot.
- **A full comparison costs a meaningful fraction of a day's quota**
  ([Budget](evaluation/method.md#budget)). Evaluation competes with the
  actual work for the same free tier.

### Open questions

Live, unresolved, and worth deciding when the evidence arrives — not before.

| Question | What would settle it |
| --- | --- |
| **Was deleting the narrow-role arm a mistake?** | `tokens_in` per run for `code` across the discriminating scenarios, read against the deleted arm's recorded 5,756 ([What to do next](#what-to-do-next-1), item 1). |
| Does the coding agent need the parts of `deepagents-code` not carried over — skills, memory, the rubric grader? | A failure the comparison produces that one of them would have prevented. Not before ([The configuration, ported from dcode](agents/code.md#the-configuration-ported-from-dcode)). |
| Which run-tree fields are worth keeping? | A metric actually reading the tree. 79% of the bytes are middleware wrappers, but "unused today" is not "surplus" ([The record: one run tree](evaluation/observability.md#the-record-one-run-tree)). |
| Default repetition count: 3 is cheap but weak, 5 costs most of a day's quota on a full suite. | The first real baseline's observed run-to-run variance. |
| Should **pinned-model mode** be the default comparison mode, with pool mode reserved for final validation? | Whether pool-mode variance actually swamps the effect sizes we care about. |
| Does the agent get a git tool? | If yes, scenarios can ship real history ("find the commit that broke this") and materialization becomes a bundle restore instead of `git archive`. |
| How does a free-pool judge get validated? | It must agree with the Claude judge on a labelled set before its scores can be trusted. |
| Where does shared cooldown state live, if it ever needs to be shared across processes? | A second concurrent consumer actually existing. |
| Where do per-provider curated docs live once the provider list grows? | The provider list growing past what one page holds ([Providers and limits](pool/providers.md)). |
| Should the web explorer's notes be read by the coding agent automatically, or only when the brief says to? | A run where the coding agent ignored a `/research/` file that answered its question ([What it is for](agents/explore.md#what-it-is-for)). |
| **Does a coding agent that *can* delegate delegate when it should?** | A `code-peers` batch against the non-delegating baseline: how many `python -m agent.explore` calls each run made, and whether the runs that made one were the ones needing outside knowledge ([What this costs, and what is unmeasured](agents/delegation.md#what-this-costs-and-what-is-unmeasured)). |
| What should a delegation's budget be? | The first observed distribution of `tokens_in` for a delegated task. Only the wall clock is bounded today (`AGENT_DELEGATE_TIMEOUT`); the token number is not invented before then ([What a delegation costs](agents/delegation.md#what-a-delegation-costs)). |

### Settled decisions

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
| ~~LangSmith is for watching, never for the record.~~ **Reopened.** The record is now the run tree LangSmith already built, *snapshotted* to disk after the run. | The requirement — a verdict rests on files on disk — is met by the snapshot. What expiry forbids is depending on the hosted copy at scoring time, not asking for the tree once while it exists ([The record: one run tree](evaluation/observability.md#the-record-one-run-tree)). |
| ~~One architecture; the conversational loop is deleted, not disabled.~~ **Reopened.** | Its inputs changed: the SDK now ships summarization and offloading, and the floor for coding work is 128,000 tokens rather than 6,000. The 39× result still stands, which is why the new arm is a configuration and not a merge ([One conversation, on the pool](agents/code.md#one-conversation-on-the-pool)). |
| Only the sync path is implemented. | Both callers are sync. A hand-written async loop was built, found unused, and removed. |
| ~~The two agents meet on disk and nowhere else; no message bus, no protocol.~~ **Partly reopened.** The coding agent can ask the explorer for a report; the *deliverable* is still a file. | A human was the message bus, which does not survive an unattended run or a third agent. The request is a command line — the agent runs `python -m agent.explore` with `execute` — so there is still no protocol to keep in step ([Delegation](agents/delegation.md)). |
| Docker is optional until L3. | L0–L2 scenarios are authored dependency-free, so the restricted `python`/`pytest` backend suffices. |

### Explicitly out of scope

Restating [What is deliberately not built](overview.md#what-is-deliberately-not-built), because scope
creep here would be expensive: no general-purpose gateway (auth, multi-tenancy,
billing, admin UI), no paid-tier fallback, no ToS circumvention.

### How to propose a change

1. Check it against the north star. If it doesn't serve *"keep the free token
   pool available for an unattended agent"*, it doesn't get built — regardless
   of how good an idea it is in the abstract.
2. Check it against [Settled decisions](#settled-decisions) and
   [Explicitly out of scope](#explicitly-out-of-scope). If it reopens one, say what new
   evidence justifies that.
3. Make it a branch, add a configuration in [evals/configs/](../evals/configs/),
   and measure it against baseline on the suite that covers it
   ([Evaluation method](evaluation/method.md)).
4. Record the verdict in [evals/CONFIGS.md](../evals/CONFIGS.md) — win or lose.
5. Merge or drop. A change that can't be shown to help doesn't merge, and a draw
   keeps the simpler configuration.
