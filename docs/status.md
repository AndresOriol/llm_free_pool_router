[← Wiki index](README.md)

# Status and roadmap

*The running state, and where the project goes next. This is the one page
expected to change often — every other page describes design; this describes
today. Settled decisions and non-goals are in
[Overview](overview.md#settled-decisions).*

**Last updated: 2026-09-19.** Update it when a batch is recorded, a scenario is
added, or a decision lands. A status page nobody updates is worse than none.

## Where things stand

- **The pool works, and in practice it is one provider.** Failover, size-aware
  selection, cooldown, spend-aware skipping and structured failure diagnostics
  are in place, and a 503 now benches the model on every account at once
  ([Failover](pool/failover.md)). But Groq served 2 calls in the whole usage
  ledger: a Gemini day-quota wall has no second platform behind it
  ([Providers and limits](pool/providers.md)).
- **Coding runs are served mostly by the weakest tier.** Over six live runs
  (2026-09-15 → 16), 72% of the calls that did the work came from
  `gemini-3.5-flash-lite`, because the capable members were the most
  rate-limited. The router selects for *availability*, not *adequacy*. Check
  which member served a run before blaming its prompt.
- **Three agents, one command line each.** The coding agent
  ([agents/code](agents/code.md)), the web explorer
  ([agents/explore](agents/explore.md)) and the improvement agent
  ([agents/improve](agents/improve.md)) share the pool; any of them can run
  another as a command ([Delegation](agents/delegation.md)), and all three can
  be served over HTTP ([Serving](operations/serving.md)).
- **The coding agent has been measured, and three prompt changes were
  shipped** on the `session` suite: the invariant guard, the written account,
  and the step budget ([Where the numbers
  stand](#where-the-numbers-stand)). Its cost is **330k–690k input tokens per
  run**, against 5,756 for the narrow-role arm deleted in August — the gap is an
  accepted risk, not a closed question
  ([The arm that was deleted](agents/code.md#the-arm-that-was-deleted)).
- **Behaviour changes now start from a probe.** The turn a recorded run went
  wrong is frozen as an example in a topic dataset, shown red, and fixed until
  it is green ([Changing how an agent behaves](evaluation/changing-behaviour.md)).
  The first dataset pins the explorer's handling of "X does not exist" claims
  (2026-09-19).
- **The improvement agent's verdicts are right; the issues it writes are not
  yet.** A diagnose-only pass on 2026-09-18 correctly reopened an issue from
  fresh runs, and filed two new issues that did not survive a check against the
  lever's history. Treat issues it opens as drafts
  ([The improvement agent](agents/improve.md)).
- **Twelve scenarios across eleven topics, L0 to L3, all pushed** to the
  `agent_evals` remote ([The catalogue](evaluation/scenarios.md#the-catalogue)).

## What's built

| Phase | State | What it covers |
| --- | --- | --- |
| **P0** trace capture | **done** | `EVAL_TRACE_FILE` JSONL, summed by every automatic metric, plus the LangSmith run tree snapshotted beside it ([Observability](evaluation/observability.md)) |
| **P1** runner | **done** | materialize → run → verify → integrity → record, plus `validate`, `show`, the catalogue and the train/holdout split ([Evaluation method](evaluation/method.md)) |
| Probes | **done** | One situation, one decision, pushed to LangSmith datasets by topic ([Probes](evaluation/probes.md)) |
| **P2** judge | not started | `claude -p` with a pinned rubric and diff-hash cache. Quality is read by hand until then |
| **P3** compare/report | partial | `show` and the catalogue; no leaderboard or written comparison |
| **P4** scenario library | **12** | L0 (exhausted), L1–L3; `long-context` and `generative` still unwritten — the catalogue's **Gaps** section derives the list |
| **P5** SWE-bench L3 | not started | Needs Docker. Deliberately last |

## Where the numbers stand

Interleaved on the `session` suite, one arm against `code` on the same pool.
Full tables and caveats are in [evals/CONFIGS.md](../evals/CONFIGS.md).

| Configuration | Batch | Result | Verdict |
| --- | --- | --- | --- |
| `code-invariant-guard` | 40 runs, 2026-09-05 | weakened a protected file 4 → 1; `count-and-share` 0/8 → 3/4 | **promoted** |
| `code-account` | 20 runs, 2026-09-06 | wrote an account 2/10 → 8/10; solved 6 → 7 of 10; cost flat | **promoted** |
| `code-step-budget` | 20 runs, 2026-09-07 | solved 8/10 both; `tokens_in` −11% | **shipped**; the prompt half earned the −11%, the 400-step budget is **unmeasured** — no scenario ran past 30 steps |
| `code-tool-sequence` | 6 runs, 2026-09-17 | — | merged, **not in the ledger** |
| `code-peers` | — | never run | no verdict |

Three things carry over from earlier batches:

1. **The pass column is noise at these sample sizes.** A configuration scored
   3/3 and 1/3 on consecutive batches on the old L0
   ([The pass column is noise](agents/code.md#the-pass-column-is-noise)). The
   evidence that promoted each change above is one scenario that failed the
   same way on every exposure, not a set-wide rate.
2. **`retry-after-case` (L0) is exhausted** as an instrument; do not re-run
   configurations against it.
3. **Most failures are `reasoning`.** Each configuration found the file, edited
   it, ran the tests, and got the fix wrong
   ([Every failure is `reasoning`](agents/code.md#every-failure-is-reasoning)).

## Blockers

| Issue | Impact |
| --- | --- |
| **`bad_tool_calls` reads zero on every run** | Not a result. [trace.py](../agent/utils/trace.py) records `ok=True` on every `tool_end`, discarding the status the tool already set, and the soft-error check matches `"Error"` against tools that return lowercase `error:` ([design note](design/generative-scenarios.md#6-corrections-to-the-hardening-plan)) |
| **The step budget has never been exercised by an eval** | Its only evidence is one live session that spent all 360 supersteps and committed a handover. `ui-port-to-typescript` (L3) was built to be long enough; the comparison has not been re-run on it |
| **The set is a repair set** | Every scenario is a broken seed plus a hidden test. "Build X" requests are probed by nothing, and the empty-patch gate degenerates on them ([design note](design/generative-scenarios.md)) |
| **The capable tier is rarely what serves** | A prompt change is measured on whatever model was free. Nothing floors capability the way `CONTEXT_FLOOR` floors context |

## What to do next

In order. The ordering is the argument.

1. **Record the `code-tool-sequence` batch** in the ledger, win or lose.
2. **Re-run `code` against `code-step-budget` on `ui-port-to-typescript`**, the
   one scenario long enough to reach the budget.
3. **Make `bad_tool_calls` real**: record the tool's own status in the trace.
4. **Turn trace reviews into probes.** Every post-mortem that finds the turn a
   run went wrong should leave a probe behind
   ([Changing how an agent behaves](evaluation/changing-behaviour.md)).
5. **Decide what a capability floor looks like** for coding runs, before
   reading any more prompt comparisons as model-independent.
6. **Run `code-peers` against `code`** — delegation is on by default and
   unmeasured.
7. **Keep authoring scenarios**: `long-context`, then the first generative one.
8. **Then** the judge (P2), then compare/report (P3).

## The two phases of the project

**Phase 1 — the pool (done enough).** Pooled free-tier capacity boring and
reliable enough that an agent can run unattended on it.

**Phase 2 — the standing maintainer (in progress).** A session that works one
project unattended on its own branch, keeps its docs current, and writes an
account a human reviews instead of the diff
([design note](design/long-run-harness.md)). Of its daily cycle, the account
(`code-account`) and the wrap-up that commits when the budget runs out
(`code-step-budget`) exist; a journal that survives a crash does not.

**The harness is not bespoke.** The narrow-role arm is deleted and `agent/code/`
is a `deepagents-code` configuration. That was a decision about what to
maintain, taken against a measurement that favoured the deleted arm on cost. A
coding session routes only to members holding at least 128,000 input tokens
([Size-aware selection](pool/failover.md#size-aware-selection)), which is why
Groq's 8,000-token members stay in the pool for other work.

## Known constraints that shape the roadmap

Facts about the environment, not problems to solve by cleverness. A plan that
ignores one of them is wrong.

- **Groq's free tier is one request budget per account**, not per model; more
  Groq *accounts* buy capacity, more Groq *models* do not
  ([Priority tiers](pool/model.md#priority-tiers)).
- **Gemini is the mirror image**: large token budgets, few requests per day. A
  wide-context session is request-bound, not token-bound
  ([Current free-tier limits](pool/providers.md#current-free-tier-limits)).
- **A conversation is expensive.** A `session`-suite run costs 330k–690k input
  tokens and about 20 provider calls; a full comparison costs a meaningful
  fraction of a day's quota and competes with real work for it
  ([Budget](evaluation/method.md#budget)).
- **Cooldown state is per process**, and so is a model's retirement. Two
  concurrent agents each rediscover which accounts are hot, and a model retired
  upstream costs one attempt per process until it is removed from the config.

## Open questions

Worth deciding when the evidence arrives — not before.

| Question | What would settle it |
| --- | --- |
| **Was deleting the narrow-role arm a mistake?** | Cost per *solved* task for `code` on the discriminating scenarios, read against the deleted arm's recorded 5,756 input tokens |
| Should routing floor capability, not only context? | A comparison of the same prompt served by the flash and lite tiers |
| Which run-tree fields are worth keeping? | A metric that actually reads the tree. 79% of the bytes are middleware wrappers, but "unused today" is not "surplus" ([The record: one run tree](evaluation/observability.md#the-record-one-run-tree)) |
| Default repetition count: 3 is cheap but weak, 5 costs most of a day on a full suite | Observed run-to-run variance on the scenarios that discriminate |
| Should **pinned-model mode** be the default for comparisons? | Whether pool-mode variance swamps the effects being measured — the lite-tier finding suggests it may |
| How does a free-pool judge get validated? | Agreement with the Claude judge on a labelled set |
| Does a coding agent that *can* delegate do so when it should? | A `code-peers` batch: how many delegations each run made, and whether those runs needed outside knowledge ([Delegation](agents/delegation.md#what-this-costs-and-what-is-unmeasured)) |
| What should a delegation's budget be? | The first observed distribution of a delegated task's `tokens_in`; only the wall clock is bounded today ([What a delegation costs](agents/delegation.md#what-a-delegation-costs)) |
| Where does shared cooldown state live? | A second concurrent consumer actually existing |
