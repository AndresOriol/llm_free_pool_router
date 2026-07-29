# Evaluating agent configurations

How we decide whether a change to the harness (router config, system prompt,
backend, agent loop) actually made the agent better. The unit of comparison is
an **agent configuration**, not a single run: free-tier failover makes any one
run noisy, so a claim like "this branch is better" only means something as a
distribution over repeated runs on a fixed set of scenarios.

This doc is the design. The harness that implements it lives in a **separate
repo** (`agent_evals`, sibling of this one) — see [Why a separate repo](#why-a-separate-repo).
Nothing here is built yet; scenarios are deliberately deferred to a later
session.

## Prior art, and why we still build

Public SWE-agent benchmarks are calibrated for frontier models on real
repositories. We are running pooled free-tier 8B–70B models, which puts us in a
different capability band — the reason we borrow their *design* freely but not
their *task sets* as a primary instrument.

| Project | Verdict | Rationale |
| --- | --- | --- |
| [SWE-bench](https://github.com/princeton-nlp/SWE-bench) | **borrow schema, defer tasks** | `FAIL_TO_PASS`/`PASS_TO_PASS` and the empty-patch/gold-patch validation gate are adopted below. The tasks themselves are L3: expect near-zero pass rates at first, so they can't discriminate between configs yet. Also needs per-repo Docker images to pin dependencies. |
| [eth-sri/agentbench](https://github.com/eth-sri/agentbench) | **read before writing our runner** | Closest prior art to this design: repo-level agent harness with `NONE`/`LLM`/`HUMAN` context settings, generate → evaluate → analyze pipeline, trace *and cost* analysis. Its context settings become our `context_mode`. Read its `evaluate.py`/`analyze.py` before building P1 — some of it may be usable directly. |
| [langwatch/scenario](https://github.com/langwatch/scenario) | **adopt for one category** | Python, integrates via a single `AgentAdapter.call()`. Its user-simulator agent is the only credible way to auto-grade the **ambiguous** category — does the agent ask a clarifying question or invent requirements? Its judge is a duplicate of ours; use only the simulator. |
| [Galileo eval-engineer](https://github.com/Galileo-Agent-Labs/eval-engineer) | **steal the workflow, skip the platform** | Its "measured change retention" loop — diagnose from traces, bounded change, verify, keep only what evidence supports — is exactly the workflow below. But it's backed by Galileo's hosted observability, which reintroduces the dependency we're removing by keeping traces local. Its packaging idea (eval commands as Claude Code skills) is worth copying later. |
| [RepoBench](https://github.com/Leolty/repobench) | **skip** | Measures next-line code *completion* scored by Exact Match / Edit Similarity / CodeBLEU. No tools, no editing, no multi-turn. It grades a raw model, not a harness, so it cannot answer any question this doc poses. |

**Containerized execution.** SWE-bench runs patches in per-repo Docker images
because those repos need exact dependency versions. Our own L0–L2 scenarios are
authored to be dependency-free pure-Python trees, so
`RestrictedShellBackend`'s jailed `python`/`pytest` is sufficient and Docker
stays optional. Docker becomes mandatory only at L3 — which is a further reason
to sequence L3 last rather than a reason to start there.

**One adapter, not N integrations.** Every external harness here drives an
agent through either an OpenAI-compatible endpoint (agentbench supports local
vLLM-style base URLs) or a thin adapter class. If we ever want to run several of
them, the cheap move is a small shim exposing the router as
`/v1/chat/completions` — then external harnesses test the *pool*, while our own
scenarios test the *agent loop*, and neither needs bespoke wiring. Don't build
the shim until a specific harness is actually wanted; noted here so it isn't
re-derived.

## Vocabulary

| Term | Meaning |
| --- | --- |
| **scenario** | A frozen starting state of a codebase — e.g. a project with a real bug in it. Stored as an immutable git tag. |
| **task** | One prompt posed against a scenario, with a machine-checkable acceptance. One scenario carries several tasks. |
| **config** | One agent configuration: a commit of `free_coding_agent` plus any overrides (router config, env, prompt). |
| **run** | One execution of (config × scenario × task × repetition). The atomic record. |
| **suite** | A named subset of tasks, so a targeted change doesn't have to pay for the full set. |
| **verdict** | The graded result of a run: automatic metrics + integrity checks + LLM-judge scores. |

## Why a separate repo

Configurations are branches of `free_coding_agent`. If results lived in this
repo they would churn on every branch switch and conflict on every merge —
exactly the data that must be stable across all configs. So:

- `free_coding_agent` — the thing under test. Contributes only the trace
  instrumentation described in [Local trace capture](#local-trace-capture),
  and this doc.
- `agent_evals` — scenarios, runner, results, reports. Never checked out by
  the agent under test.

Results are also the answer to LangSmith traces expiring: everything the
verdict is derived from is written to disk under `results/` and committed.
LangSmith stays useful for eyeballing a single run live; it is not the record.

## Scenario storage

Each scenario is an **orphan branch plus an immutable tag** in `agent_evals`:

```
master                     runner, configs, results, reports
scenario/<id>              orphan branch — the scenario tree, editable
scenario/<id>/v1           tag — frozen; what runs actually reference
```

Orphan rather than a linear chain of commits: scenarios are unrelated
codebases, and a chain would make editing an old scenario a history rewrite of
every scenario after it. Orphan branches keep them independent while still
making "a scenario is a git state" literally true.

Tags are **immutable**. Editing a scenario means committing to
`scenario/<id>` and cutting `v2`; `v1` and every result that references it stay
reproducible. A run record always names the exact tag, never the branch.

**Materialization** is `git archive`, not checkout:

```bash
git -C agent_evals archive scenario/router-cooldown/v1 seed | tar -x -C <workdir> --strip-components=1
```

`git archive` extracts one subtree into a scratch dir with no `.git` in it.
That matters: the agent is jailed to the workdir, and a `.git` would let it
read the hidden tests, the reference solution, and every other scenario. It
also means no detached-HEAD or dirty-tree handling, and nothing to clean up.

### Scenario anatomy

```
scenario.yaml          metadata: id, title, category, tags, immutable manifest
seed/                  the ONLY thing the agent sees — copied into the workdir
tasks/<task-id>.md     one or more prompts, each with its own acceptance
verify/                hidden tests — overlaid at grading time, never in seed/
reference/             solution.patch + notes.md, for the judge and for validation
```

The `seed/` vs `verify/` split is load-bearing. If the acceptance test ships in
the workdir the agent can (and small models do) edit the test until it passes,
or overfit to it. Hidden tests are copied over a *copy* of the finished
workdir, so they apply even if the agent deleted the visible ones.

`seed/` may contain its own `CLAUDE.md` — the agent loads it as its system
prompt ([agent/coding_agent.py](../agent/coding_agent.py)). Whether a scenario
ships instructions is itself a variable worth testing; record it in
`scenario.yaml`.

### scenario.yaml

```yaml
id: router-cooldown
title: Cooldown timer never resets after a 429
category: bugfix            # bugfix | feature | refactor | tests | ambiguous | trap
difficulty: L1              # see the difficulty ladder below
tags: [python, single-file, long-context]
context_mode: none          # none | claude_md — is a CLAUDE.md shipped in seed/?
immutable:                  # files the agent must not modify; hashed pre/post
  - tests/test_cooldown.py
timeout_s: 900

# Borrowed from SWE-bench: two test sets, not one command.
fail_to_pass:               # must go from failing to passing — did it fix the thing
  - verify/test_cooldown.py::test_cooldown_resets_after_retry_after
pass_to_pass:               # must stay passing — did it break anything else
  - verify/test_cooldown.py::test_cooldown_blocks_while_hot
  - verify/test_router.py
```

`fail_to_pass` / `pass_to_pass` replaces a single verify command, and it's a
strictly better acceptance contract: "the bug is fixed" and "nothing else
broke" are different claims, and a small model that deletes an inconvenient
branch satisfies the first while failing the second. A run passes only if
every `fail_to_pass` flips **and** every `pass_to_pass` holds.

`context_mode` mirrors `eth-sri/agentbench`'s `NONE`/`HUMAN` context settings.
Since [agent/coding_agent.py](../agent/coding_agent.py) loads a seed's
`CLAUDE.md` as the system prompt, the same scenario run in both modes measures
how much the harness depends on curated context — worth knowing before
investing in more of it.

### Task file

```markdown
---
id: fix-from-failing-test
suite: [smoke, full]
tags: [bugfix, test-driven]
---

## Prompt
<the exact text piped to the agent's stdin>

## Acceptance
Inherits the scenario's fail_to_pass / pass_to_pass, unless overridden here.

## Judge notes
What a correct fix looks like; what counts as out of scope here.
```

One scenario, several tasks, is the point: the same buggy tree can pose
"here's a failing test, fix it", "users report X, find and fix it" (no test
given), and "add feature Y on top" — three very different capability probes for
one authoring cost.

### Scenario categories

The set to cover when scenarios actually get written (later session):

- **bugfix, test-driven** — failing test provided. Baseline competence.
- **bugfix, symptom only** — no test; agent must localize. Probes diagnosis.
- **feature** — spec + hidden tests. Probes multi-file construction.
- **tests** — write tests for existing code; verified by a mutation-style check
  (the tests must fail against a seeded broken variant).
- **refactor** — behavior-preserving; hidden tests must still pass. Probes
  restraint.
- **long-context** — a large file that exceeds small-TPM pool members. Directly
  probes size-based routing.
- **ambiguous** — underspecified brief. Judge-only; does the agent ask or
  invent?
- **trap** — the brief asks for something the code contradicts, or that would
  break a documented invariant. Measures over-eagerness. No auto-pass.

### Difficulty ladder

Scenarios carry an explicit difficulty, because **a task that every config
passes or every config fails carries no information**. Public benchmarks are
calibrated for frontier models; we are running 8B–70B free-tier models, so
importing their difficulty wholesale would produce a set where everything
scores zero and no two configs are distinguishable.

| Level | Shape | Expected pass rate | Role |
| --- | --- | --- | --- |
| **L0** | one function, failing test handed over | ~100% | canary — if L0 drops, the harness is broken, not the model |
| **L1** | one file, localize from a symptom | 50–90% | main comparison instrument |
| **L2** | multi-file, cross-module change | 10–50% | comparison instrument as configs improve |
| **L3** | real SWE-bench Lite instances | ~0–5% initially | ceiling probe, *not* a comparison instrument |

The `smoke` suite is drawn from wherever the current baseline sits between
roughly 20% and 80%. That band moves as the agent improves, so suite membership
is reviewed whenever a baseline is re-run — a task that saturates gets demoted
to regression duty and a harder one takes its place. Recording L3 results is
still worth it as an absolute-progress marker; just never make a promotion
decision on a metric that reads zero for both configs.

### Validation gate

Every scenario must self-test before it's usable, via `runner validate`:

1. `seed/` + `verify/` → every `fail_to_pass` test **must fail** and every
   `pass_to_pass` test **must pass** (otherwise the task is already solved, or
   the seed is broken in a way the task never mentioned).
2. `seed/` + `reference/solution.patch` + `verify/` → **all** must pass
   (otherwise the task is impossible and every config scores a free fail).
3. Every file in `immutable:` exists in `seed/`.

This is the same empty-patch / gold-patch gate SWE-bench applies to its own
instances, and it is worth running on every scenario every time the suite runs,
not just at authoring time — a dependency drift that silently makes a
`pass_to_pass` test fail on the seed would otherwise show up as every config
regressing at once.

A scenario that fails validation never enters a suite. This single gate catches
most of the ways an eval set silently stops measuring anything.

## Agent configuration

A config is a pinned commit plus overrides:

```yaml
# configs/baseline.yaml
name: baseline
repo: ../free_coding_agent
ref: master                 # branch, tag or SHA
overrides:
  router_config: null       # or a path to an alternative config.yaml
  env:
    RECURSION_LIMIT: "150"
```

Resolution: `git worktree add --detach <tmp> <ref>` from the target repo, so
the config runs from a clean tree without disturbing the working copy — you can
keep editing `master` while a comparison runs. The runner records the resolved
**SHA** plus a hash of the effective overrides as the config fingerprint;
`ref: master` today and `ref: master` next week are different configs, and the
records say so.

A config may also pin a single model (bypassing failover). That is not how the
agent ships, but it is the lowest-variance way to attribute a change to the
prompt/loop rather than to which account happened to be warm — see
[Fair comparison](#fair-comparison).

## Run lifecycle

Runs execute **serially**. Parallel runs contend for the same free-tier pool,
which both burns quota faster and makes the model mix of each run depend on the
others — destroying comparability.

1. **Materialize** — `git archive <scenario tag> seed` into a scratch workdir.
   Hash every file in the `immutable` manifest.
2. **Run** — `python -m agent.coding_agent <workdir> < prompt.txt` from the
   config's worktree, with `EVAL_TRACE_FILE` set. Kill at `timeout_s`.
3. **Capture** — stdout/stderr, wall time, exit status, `trace.jsonl`, and
   `diff.patch` (workdir vs `seed/`).
4. **Verify** — copy the finished workdir to a pristine location, overlay
   `verify/`, run the `fail_to_pass` and `pass_to_pass` sets. Record both
   ratios and the raw output.
5. **Integrity** — re-hash the immutable manifest. Any change ⇒ run marked
   `tampered`, which is a fail regardless of test outcome and is reported
   separately (it's a distinct failure mode, not the same as "got it wrong").
6. **Judge** — see below. Skipped if a run with an identical diff hash was
   already judged.
7. **Record** — write `results/runs/<run_id>/` and append one row to
   `results/index.jsonl`.

`run_id`: `<scenario>_<taskid>_<config>_r<rep>_<UTC timestamp>`.

## Local trace capture

The only change this design asks of `free_coding_agent`: when `EVAL_TRACE_FILE`
is set, write one JSONL line per interesting event. Two producers, one file:

- A LangChain `BaseCallbackHandler` attached in `build_agent` — LLM start/end
  (model name, token usage when the provider reports it), tool start/end (name,
  args, error), chain end.
- A logging handler on the `LLMRouter` logger — the routing and failover lines
  that `RouterChatModel._handle_failure`
  ([agent/router_chat_model.py:98](../agent/router_chat_model.py:98)) already
  emits, as structured records rather than prose.

Event shape: `{ts, event, model, provider, tokens_in, tokens_out, tool, ok, detail}`.
Every automatic metric below is a count or sum over this file — no log-scraping
with regexes, and no dependency on a hosted service. Off by default; zero cost
when the env var is unset.

## Metrics

### Automatic — from trace.jsonl, the diff, and verification

| Metric | Definition |
| --- | --- |
| `outcome` | `pass` \| `fail` \| `crash` \| `timeout` \| `tampered` |
| `f2p_ratio` | `fail_to_pass` tests flipped / total — partial credit on the fix |
| `p2p_ratio` | `pass_to_pass` tests still passing — measures collateral damage |
| `failure_class` | `retrieval` \| `tooling` \| `reasoning` \| `stopping` — see below |
| `steps` | agent loop iterations (tool-call rounds) |
| `provider_calls` | LLM calls including failover retries |
| `failover_bounces` | transient failures before a step succeeded — wasted quota |
| `tokens_in` / `tokens_out` | summed where the provider reports usage |
| `bad_tool_calls` | invalid tool name, failed `edit_file`, malformed args |
| `models_used` | distinct models that served a step (and the per-model call mix) |
| `ran_own_tests` | did the agent invoke `execute` on the test command itself |
| `self_corrected` | did a failing `execute` get followed by another edit |
| `files_touched` / `diff_lines` | change size, vs the reference solution's size |
| `wall_time_s` | end to end |

### Failure taxonomy

A pass rate tells you a config is worse; it doesn't tell you what to fix. Since
we know which files the reference solution touches, every failed run can be
classified automatically from `trace.jsonl`:

| Class | Signal | What it means | What fixes it |
| --- | --- | --- | --- |
| `retrieval` | never read a file the gold patch touches | couldn't find the code | better search/navigation tools, or context injection |
| `tooling` | read it, but `edit_file` calls failed to apply / `tool_use_failed` | knew what to change, couldn't express the edit | change the edit format — line-anchored or whole-file rewrite instead of exact-string match |
| `reasoning` | edits applied cleanly, tests still fail | wrong fix | stronger model tier; better prompt |
| `stopping` | hit recursion limit, or declared done without running tests | loop problem | step budget, prompt, forcing a self-test before finishing |

This is the highest-leverage metric in the whole set, and it's the one that
answers the "bad retrieval vs. bad editing" question that trajectory inspection
is usually needed for — cheaply, for every run, without reading transcripts.

The reason it matters most *here* specifically: on small models the `tooling`
class is likely to dominate. Groq already returns `tool_use_failed` with the
model's raw malformed output, which
[RouterChatModel](../agent/router_chat_model.py:98) surfaces today. If the
taxonomy shows that a large share of failures are malformed edits rather than
wrong reasoning, then changing the edit tool's format is a bigger win than any
model or prompt change — and that is not a conclusion you would reach by
staring at a pass rate.

### Judged — `claude -p` with a fixed rubric

The judge sees: the task prompt, `diff.patch`, verification output,
`reference/solution.patch`, and the scenario's judge notes. It returns JSON
scoring 0–4 on:

- **correctness beyond tests** — right for the right reason, or coincidence?
- **scope discipline** — did it change only what the task asked for?
- **code quality** — does it read like the surrounding code? (this repo's
  `ponytail` bar)
- **instruction adherence** — did it follow explicit constraints in the brief?

Three rules make judge scores comparable across time:

1. **Blind** — the judge is not told which config produced the diff, and runs
   are shuffled before judging.
2. **Pinned** — the judge model id and rubric prompt version are recorded in
   every verdict. Changing either starts a new comparison epoch; you cannot
   compare judged scores across epochs, only re-judge.
3. **Diff-addressed** — verdicts are cached by diff hash, so identical outputs
   score identically and repeat judging is free.

The judge is deliberately *not* shown the agent's transcript: the model that
writes a convincing narrative and the model that writes a correct patch are not
the same model, and we're grading the patch.

### What the axes are for

- **Task success** → `outcome`, `f2p_ratio`, `p2p_ratio`. The only gating axis.
- **Diagnosis** → `failure_class`. What to actually work on next.
- **Autonomy** → `ran_own_tests`, `self_corrected`. Closing its own loop is the
  difference between an agent and a code generator.
- **Efficiency** → `provider_calls`, `failover_bounces`, `tokens_*`. On a free
  pool this *is* the cost model: a run that passes but drains the pool is a weak
  pass.
- **Robustness to failover** → `models_used` vs `outcome`. Did switching model
  mid-task derail it?
- **Integrity** → `tampered`. Non-negotiable; tracked separately so it can
  never be averaged away.
- **Quality** → judge scores.

## Fair comparison

The pool picks a different model per step depending on which accounts are warm
*at that moment*. Two threats follow, and both are design problems, not caveats:

**Confounding by pool state.** Config A run at 09:00 on a fresh pool and config
B at 09:30 on an exhausted one are not comparable. Mitigations, in order of
strength:

1. **Interleave** — execute `A,B,A,B,…` per (task, rep), never all of A then
   all of B. Quota drift then hits both configs roughly equally.
2. **Report the model mix** per config. If the mixes differ materially, the
   comparison is flagged confounded in the report rather than quietly reported
   as a win.
3. **Pinned-model mode** for changes where the pool isn't the variable (prompt,
   loop, tools). Much lower variance; validate the winner on the real pool
   afterwards.

**Sample size.** With N repetitions per task, a suite of T tasks gives N×T
trials. Default N=3 for a quick read, N=5 when promoting a change. Report
success rate with a Wilson interval, and treat any difference whose intervals
overlap as no difference. Concretely, at N=5 × 6 tasks (30 trials), differences
under roughly 15 points are noise — don't ship on them.

**Promotion rule.** Promote a config over the baseline when: no task regresses
by more than one trial, **and** either success rate improves beyond the interval
overlap, or success rate holds flat while a secondary metric (`provider_calls`,
`failover_bounces`, judge quality) improves materially. Everything else is a
draw, and a draw means keep the simpler config. Calibrate these thresholds after
the first real baseline — they are a starting heuristic, not a measurement.

## Suites and selective running

Not every change needs the full set. Tasks carry `suite` and `tags`, and the
runner filters:

```bash
python -m runner run --config my-branch --suite smoke --reps 3
python -m runner run --config my-branch --tags long-context --reps 5
python -m runner compare --configs baseline,my-branch --suite smoke
```

- `smoke` — ~4 tasks, mixed categories, the cheapest thing that would catch a
  real regression. Run on any change.
- `full` — everything. Run before promoting a config.
- tag-scoped — e.g. a routing change runs `--tags long-context`; a prompt change
  runs `--tags ambiguous,trap`.

The tag→axis mapping is what makes selective running honest: a tag exists so
that "I changed X, so I ran the tasks that exercise X" is a checkable claim.

## Storage layout

```
agent_evals/
  runner/                     the harness
  configs/<name>.yaml         agent configurations
  results/
    index.jsonl               one row per run — the queryable table
    runs/<run_id>/
      run.json                config fingerprint, scenario tag, metrics, outcome
      trace.jsonl             captured events
      stdout.log              raw agent output
      diff.patch              seed → final workdir
      verify.txt              hidden-test output
      judge.json              rubric scores + judge model/prompt version
  reports/<date>-<topic>.md   written conclusions
  CONFIGS.md                  ledger: every config tried, verdict, why kept/dropped
```

`index.jsonl` is append-only and holds every field needed to build a
leaderboard, so comparison is a small script over one file. The per-run
directory keeps the evidence behind each row — that's the part LangSmith loses.

Ranking is **lexicographic, not a weighted score**: success rate → integrity
clean → judge quality → provider calls. Collapsing these into one number
requires inventing weights, and the weights would be doing the deciding. Keep
the columns visible.

## How this changes the development workflow

1. Establish a baseline: run `full` on `master`, commit results.
2. Make a change on a branch. Add a config for it.
3. Run the suite that covers the change (interleaved against baseline).
4. Read the comparison. Apply the promotion rule.
5. Write `reports/<date>-<topic>.md` — what was tried, the numbers, the call.
   Add a row to `CONFIGS.md`, including for changes that *lost*: knowing what
   didn't work is most of the value of keeping the data.
6. Merge or drop. A change that can't be shown to help doesn't merge.

This is the point of the whole system: the harness stops being improved by
plausible reasoning and starts being improved by measurement.

## Budget

A single task run is roughly 20–60 provider calls. `smoke` at 4 tasks × 3 reps ×
2 configs ≈ 24 runs, i.e. several hundred to ~1500 calls, executed serially —
that is a meaningful fraction of a day's free-tier allowance, and runs will hit
cooldown waits. Plan comparisons as a background batch, not an interactive loop.
Judging is the only paid component; diff-hash caching keeps it to one call per
distinct patch.

## Build order

- **P0** — trace capture in `free_coding_agent` (`EVAL_TRACE_FILE`). This is the
  *measuring instrument*, not a candidate change: it must land on `master` so
  the baseline itself can be measured. One L0 and one L1 scenario authored by
  hand and driven end to end manually, to validate the formats before
  automating them.
- **P1** — read `eth-sri/agentbench`'s evaluate/analyze pipeline, then build
  `runner`: materialize, run, verify (`fail_to_pass`/`pass_to_pass`), integrity,
  record, `index.jsonl`, `validate`. Metrics automatic, including the failure
  taxonomy; judging still manual.
- **P2** — `claude -p` judge with the pinned rubric and diff-hash cache.
- **P3** — `compare` / report generation, leaderboard, interleaved execution.
- **P4** — scenario library filled out across L0–L2 per the category list;
  `langwatch/scenario` adapter for the ambiguous category.
- **P5** — L3: SWE-bench Lite instances behind Docker, as an absolute-progress
  marker. Optionally the OpenAI-compatible router shim, if driving external
  harnesses is wanted by then.
- **Later** — free-pool judge, validated for agreement against the Claude judge
  on a labelled set before it replaces it.

## Open questions

- Default N: 3 is cheap but weak; 5 costs most of a day's quota on a full suite.
  Decide after the first baseline shows actual run-to-run variance.
- Does the agent get a git tool? If so, scenarios can ship real history
  ("find the commit that broke this") and materialization becomes a bundle
  restore instead of `git archive`.
- Whether pinned-model mode should be the *default* comparison mode, with pool
  mode reserved for final validation.
- How a free-pool judge gets validated: it needs to agree with the Claude judge
  on a labelled set before its scores can be trusted.
