# Design note: evaluating generative requests

*Written on `harness/eval-generative`, 2026-09-05. Companion to the offline
hardening plan, which it does not repeat: that plan fixes the instrument, this
one widens what the instrument is pointed at. Where the two disagree,
[§6](#6-corrections-to-the-hardening-plan) says which is right, because six of
its findings were re-verified against this tree and two of them had changed.*

---

## 1. The set measures repair, and only repair

Five scenarios exist. Four are a broken seed plus a hidden test that pins the
fix; the fifth is a `feature` in name whose shape is still "make these six
assertions flip". Of the eight categories
[9.6](../09-scenarios.md#96-categories-to-cover) names, **five have never been
written** — `tests`, `refactor`, `long-context`, `ambiguous`, and the one this
note adds.

That is not an authoring backlog. It is a property of the oracle. Every part of
the harness assumes a **before** state that is wrong in a specific way:

| Mechanism | The assumption it encodes |
| --- | --- |
| `fail_to_pass` | there is a behaviour that fails today and must pass tomorrow |
| `pass_to_pass` | there is a working system to avoid breaking |
| `validate`'s empty-patch gate | the hidden tests **fail** on the untouched seed |
| `gold_files` / `failure_class` | there is one reference patch, touching known files |
| `immutable` | the interesting risk is the agent editing the test |

A request like *"build a CLI that ingests these CSVs and reports monthly
totals"* satisfies none of them. There is no before state, the reference patch
is one of many acceptable implementations, and `retrieval` — "did it open a file
the gold patch touches" — is meaningless when the files do not exist yet.

**Why this matters beyond coverage.** The one load-bearing result the project
has is that **every recorded failure is `reasoning`** — 12 of 13, zero
`retrieval`, zero `tooling` ([11.3](../11-eval-status.md#113-where-the-numbers-stand)).
That was measured entirely on repair tasks where the agent was handed the file
or a failing test naming it. It is a statement about this set, not about the
agent, and it is exactly the statement a generative set would test.

---

## 2. What the field actually does, and what checked out

Surveyed against primary sources; everything below was verified unless marked
otherwise.

**CursorBench is real and is not adoptable as a method.** It is Cursor's
internal suite for deciding which models they ship
([blog](https://cursor.com/blog/cursorbench),
[leaderboard](https://cursor.com/cursorbench)). What is public and useful: tasks
are deliberately terse and underspecified, drawn from real sessions via **Cursor
Blame**, which traces committed code back to the agent request that produced it;
tasks are multi-file and largely from Cursor's own codebase to limit
contamination; categories grew from edit/refactor/bugfix to include codebase
understanding, bugfinding, planning, review, instruction following and tool use.
What is not public: the grading. One line — *"we use agentic graders"* — and no
rubric, no dataset, no agreement-with-human figures, nothing reproducible.

So the transferable idea is the **sourcing**, not the scoring. Recovering "the
prompt that produced this commit" is a scenario factory, and this repo will have
the raw material for it once sessions are logged beside their diffs. That is a
roadmap item, not a next step.

Three numbers from elsewhere shape every decision below:

- **Hiding the tests is worth about 19 points.** NL2Repo-Bench ran the ablation:
  showing Sonnet 4.5 the hidden suite moved it from 40.2% to 59.4%. The
  withholding this repo already does is the single most valuable property of the
  harness, and nothing here may weaken it.
- **Weak suites manufacture false passes.** SWE-bench+ found ~31% of passing
  instances rested on insufficient tests, and 32.67% of issues leaked the
  solution in their own text. UTBoost's added tests flipped rankings for 24.4% of
  SWE-bench Verified submissions.
- **Over-specification is the dominant authoring failure.** The Terminal-Bench
  task-quality work found **>15% of tasks demonstrably reward-hackable**, and a
  code-editing audit found 11 of 15 universally-unsolved problems were benchmark
  artifacts rather than model limits — including a suite rejecting Python's own
  recommended logging idiom because a literal string was absent from the source.

**LLM-as-judge is not the answer here, and the reason is specific.** Documented
self-inconsistency on repeated identical grading, self-preference bias, and
prompt-perturbation swings in human correlation of up to 0.2 *for smaller
models* — which is precisely the model class a free pool can afford. A judge on
this pool would also spend the quota the agent needs
([13.3](../13-roadmap.md#133-known-constraints-that-shape-the-roadmap)). The judge
(P2) stays where [11.5](../11-eval-status.md#115-what-to-do-next) put it: a
diagnostic, never a gate.

---

## 3. The oracle for a generative task

### 3.1 The empty-patch gate degenerates, and must be replaced

On a repair scenario the gate is load-bearing: `fail_to_pass` must fail on the
untouched seed, which proves the tests actually detect the defect. On a
generative scenario the module does not exist, so **everything** fails at import
— the gate passes trivially and proves nothing at all about test quality. A
scenario whose hidden suite asserts nothing useful would sail through it.

What carries the weight instead is a **second implementation**:

> Every generative scenario ships `evaluation/solution.patch` **and**
> `evaluation/solution_alt.patch`, written to the same spec by a different
> author or model and deliberately different in structure. Both must pass every
> `fail_to_pass` and `pass_to_pass` test.

An alternative that fails is not a bug in the alternative — it is proof the
suite is coupled to *an* implementation rather than to the requirement, which is
the failure mode that produced every cautionary result in
[§2](#2-what-the-field-actually-does-and-what-checked-out). It costs one extra
offline implementation per scenario, once, and it is the highest-value item in
this note.

### 3.2 Scoring is a tier, not a boolean

A free-tier pool asked to build something from scratch will fail the behavioural
suite for months. A column that reads `0/0/0/0` across every configuration
carries no information — the same argument
[9.7](../09-scenarios.md#97-the-difficulty-ladder) makes about difficulty,
applied to the oracle itself. So a generative run is scored on an ordered
ladder, each rung a separate recorded field:

| Tier | Gate | What it separates |
| --- | --- | --- |
| `imports` | the package imports and the entry point runs | produced *something* executable |
| `contract` | the visible API-shape tests pass | got the interface right |
| `behaviour` | `fail_to_pass` — the hidden behavioural suite | got the requirement right |
| `intact` | `pass_to_pass` — invariants and constraints hold | did not break the deal to get there |

`outcome: pass` still means every rung. The tiers are what make the months
before that legible, and each is a deterministic, zero-token check.

**The visible contract tests are a deliberate leak.** Two to four tests ship in
the seed, asserting only the API shape — names, signatures, return types — never
behaviour. Without them a generative task is unfair rather than hard: an agent
that builds a correct thing under a different name scores zero for a reason that
has nothing to do with capability. The behavioural suite stays withheld, so the
19-point leakage figure is not paid.

### 3.3 Two more gates, both free

- **Branch coverage over the region the solution touches.** Reject a hidden
  suite that does not exercise what the reference implementation writes.
  BigCodeBench holds 99% branch coverage at 5.6 tests per task; the audit found
  59% of one benchmark's low-coverage suites could not detect changes outside the
  edit region. `pytest-cov`, run at authoring time.
- **Behaviour, never source.** Assertions read return values, exit codes, file
  contents and stdout. Never identifiers, imports, call graphs, or the presence
  of a string in a file. This is a review rule, and the one place the existing
  set already bends it: `duration-notes` and `threshold-off-by-one` assert on
  README and `docs/alerts.md` *text*, which is defensible only because keeping
  the docs true **is** the requirement there.

### 3.4 One paid gate, at authoring time only

**The exploit audit.** Once per scenario, on a strong model outside the pool,
run an agent told to make the hidden tests pass *without* implementing the
feature — without showing it the tests or the reference. Anything it finds is a
scenario bug. This is what surfaced the >15% reward-hackable figure in
Terminal-Bench, it costs one strong-model run, and it amortises over every eval
run the scenario will ever serve.

---

## 4. Where generative scenarios come from

Ranked by scenarios per hour of authoring, with what makes each trustworthy.

**1. Strip-to-stubs from a repo you already have (Commit0's transform).** Parse
each file to AST, replace every public function body with `pass`, delete private
functions, hand over a rendered spec plus the visible contract tests. Fully
mechanical, no model in the loop, and the upstream test suite is the oracle —
written by the code's author against the code's intent, which is exactly the
property that makes it a fair judge. **This is the pipeline to build first**, and
this repo is its own best corpus: `llm_router/quota/`, `agent/protocol/` and
`evals/verify.py` are self-contained, well-tested modules that would each strip
into a scenario.

*Trustworthy because* the tests predate the task and were never written with an
agent in mind. *Untrustworthy when* the module's docstrings amount to the
implementation, or when the upstream suite is thin — check coverage before
accepting.

**2. Revert a real feature commit.** Seed is the parent commit; the oracle is
the commit's own test files; the prompt is the PR title and body. *Untrustworthy
when* the description contains the diff — the 32.67% leakage figure is exactly
this, so the prompt must be rewritten to state the requirement without the
solution.

**3. Spec-from-repo reverse engineering (NL2Repo-Bench's pipeline).** Extract a
structured spec — description, supported features, API guide, implementation
notes — check by AST that every core API appears with the right signature, then
pilot on a strong model and triage failures into *spec ambiguity*, *environment
bug*, and *genuine difficulty*. The highest-quality pipeline surveyed and
explicitly annotator-in-the-loop: it does not automate, and pretending otherwise
is how a set stops measuring anything.

**4. Procedural bug-minting (SWE-smith, R2E-Gym).** Build the environment first,
then mint bugs into it and keep the mutants that break existing tests. Noted for
completeness and **not adopted**: both are repair-task factories, and repair is
the thing this set already has too much of.

Whichever produces a scenario, the acceptance path is the same: `validate`, the
two-implementation gate, the coverage check, then the exploit audit.

---

## 5. What changes in the harness

Ordered by cost. The first three are done on this branch.

**5.1 A human page per scenario, withheld.** `evaluation/scenario.md`, four
fixed sections — *The seed*, *The task*, *The challenge*, *What it checks*. It
lands under `evaluation/`, so the existing filter withholds it with **no code
change**: `HIDDEN` matches the first path component only
([scenario.py:40](../../evals/scenario.py)), which is also why it must not go
anywhere near `docs/` — that is visible seed content in three scenarios, and in
`count-and-share` `docs/ledger.md` *is* the trap.

**5.2 A generated catalogue on the scenario repo's `master`.** `python -m evals
index` renders `docs/scenarios/README.md` from the tags: every scenario, its
branch, category, level, tasks, test counts, and its page. `--check` fails when
it has drifted. The hand-kept table it replaces was already wrong —
`topic/pipeline` had existed as a branch with no scenarios and no row, which is
what a hand-kept index of a growing set does. The catalogue's **Gaps** section is
the part that earns it: uncovered categories and levels, tagless topic branches,
scenarios with no page, and scenarios with an empty `fail_to_pass`.

This is the one thing that ever writes to the scenario repo, and it writes on a
branch no run materializes — which is what lets it repeat the withheld material.
`validate` now refuses a scenario whose tree contains that path, so the day
someone roots a topic branch on a commit carrying the catalogue, the run stops
instead of quietly handing the answers over.

**5.3 An empty test set is a validation failure.** A `scenario.yaml` with
`fail_to_pass` misspelled defaults to `[]`, and then reads clean through every
check in `validate` — `0 > 0` is false, `0 != 0` is false, and the reference
solution "passes". The scenario scores every configuration a free pass, forever,
silently. One check closes it.

**5.4 The tier ladder.** `verify` records `imports`, `contract`, `behaviour`,
`intact` as separate fields; `outcome` keeps its current meaning. New
`scenario.yaml` keys, all optional so existing scenarios are untouched:
`entry_point` (what must import and run), `contract_tests` (visible, in the
seed), `solution_alt` (the second implementation). `validate` requires the
alternative for `category: generative` and for nothing else.

**5.5 The taxonomy needs a generative arm.** `retrieval` is undefined when the
gold files do not exist yet, and `stopping` currently absorbs it. The failure
classes for construction are *did not produce a runnable artifact*, *wrong
interface*, *wrong behaviour*, *broke a stated constraint* — which is the tier
ladder read as a taxonomy, and costs nothing extra once the tiers are recorded.

---

## 6. Corrections to the hardening plan

Its six findings were re-verified against this tree by execution, not reading.
Four hold, two changed, and one is worse than it was described.

| Finding | Verdict | What actually holds |
| --- | --- | --- |
| Deletions vanish from `files_touched` | **holds** | Reproduced on a real `git diff --no-index` deletion: `files_touched: 0`. But `classify_failure` tests retrieval *before* `touched`, so `stopping` only follows when `_read_gold` passes — otherwise it reads `retrieval`. The `/dev/null` guard at [metrics.py:43](../../evals/metrics.py) is unreachable dead code: `^\+\+\+ b/` cannot match `+++ /dev/null`. 8 of 69 recorded runs already carry `files_touched == 0`. |
| Retrieval matches on basename | **holds, understated** | It is not a basename compare — it is a **substring test against the raw serialized args**. A `grep` pattern merely containing the characters `__init__.py` satisfies retrieval. |
| `self_corrected` fires on `error` | **holds, broader** | The condition is `"fail" in output or "error" in output`, so `1 xfailed` and `failed=0` both trip it, and the flag is never cleared — one match at step 1 makes every later edit count. 6 of 69 runs report it; none is trustworthy. |
| `bad_tool_calls` rests on prose | **holds, and it is the worst of the six** | Measured over the recorded corpus: **0 hits against 94 error-shaped outputs across 1,093 `tool_end` events**, and `bad_tool_calls == 0` in all 69 `run.json`. Two causes: [tools.py](../../agent/runtime/tools.py) returns lowercase `error:` against a case-sensitive `startswith("Error")`, and deepagents tools return a `ToolMessage` whose repr starts `content=`. **The fix is upstream, not in the metric**: [trace.py:159](../../agent/runtime/trace.py) hardcodes `ok=True` on every `tool_end`, discarding a `status` field the tool already sets. Record the status and the metric follows. |
| The stubs are dead code | **changed** | Nothing *automated* runs them, and there is no CI — but they are documented in [CONFIGS.md](../../evals/CONFIGS.md), have four recorded runs, and **work today**: all five produce their intended outcome and failure class, offline, in under a second. They are a ready-made regression suite, not an unfinished one. |
| `code.yaml` pins a stale branch | **changed — worse** | `harness/deepagents` does not resolve on this clone, so `agent_config.load` **raises** and `--config code` cannot start at all; same for `code-peers` on `harness/agent-protocol`. Both branches were merged into `master` and deleted locally. Not a footgun — a hard stop, and it means no eval run of the shipping agent could have been attempted. **Fixed on this branch**: both now pin `master`. |

Two findings the plan did not have:

- **`input_tokens` did not exist.** [CLAUDE.md](../../CLAUDE.md),
  [6](../06-agent.md), [11](../11-eval-status.md), [13](../13-roadmap.md),
  [16](../16-agent-protocol.md), `CONFIGS.md` and both agent configs named
  `input_tokens` as the column to read first on every batch. The key
  `metrics.py` emits is **`tokens_in`**, so anyone — or any agent — following
  the documented instruction got nothing. Renamed across all nine places on this
  branch; `max_input_tokens`, which is a real router field, is untouched.
- **Three test files collect zero tests, and not for the reason assumed.** It is
  3 of 14, not 2 of 15, and the cause is not a missing `__init__.py` — all three
  packages have one and collection exits clean. `test_quota.py`,
  `test_usage.py` and `smoke_test.py` name every check `_check_*` and drive them
  from `__main__`, so pytest imports them and finds nothing. ~400 lines of
  working coverage that **passes when run as a script** and would stay
  invisible-green under a CI that ran only pytest. The two-line shim the
  companion audit proposes is what turns it back on.

---

## 7. Sequencing

The hardening plan's Phase A stays first and unchanged in intent: nothing here
is worth measuring through instruments that are wrong. Its cheapest item is now
also its most valuable — `tests/evals/test_runner_end_to_end.py` over the five
stub configs, which are *proven working*, would have caught two of the four
metric defects on the day they were written.

| Step | Cost | Why here |
| --- | --- | --- |
| Fix `ok` in `trace.py`, then the four metrics | none | `bad_tool_calls` reads 0 across the whole corpus; every trajectory number downstream inherits it |
| CI, plus the two collection shims | none | 400 lines of coverage are dark and every check in this repo is manual |
| Assert the stub outcomes in a test | none | The end-to-end path is exercised only by someone remembering to |
| The tier ladder and `solution_alt` | none | Schema and offline arithmetic; blocks the first generative scenario |
| **First generative scenario, strip-to-stubs from this repo** | authoring only | Proves the pipeline; the first thing the set has that is not repair |
| Exploit audit on it | one strong-model run | Before it is trusted, not after |
| `long-context` | new runs | Still the largest architectural gap ([11.4](../11-eval-status.md#114-blockers)) |

---

## 8. Out of scope

- **The judge as a scorer.** [§2](#2-what-the-field-actually-does-and-what-checked-out).
  It remains P2, diagnostic, non-gating.
- **End-to-end browser grading.** SWE-Lancer's Playwright approach is right and
  the wrong economics here: slow, flaky, and orthogonal to what a small model
  fails at today.
- **Procedurally minted repair tasks.** [§4](#4-where-generative-scenarios-come-from).
- **Blame-style sourcing from this repo's history.** Needs sessions logged
  beside their diffs. Roadmap, not next step.
- **Collapsing the tiers into a score.** They are columns. Ranking stays
  lexicographic ([10.5](../10-metrics.md#105-ranking-is-lexicographic-not-weighted)).
