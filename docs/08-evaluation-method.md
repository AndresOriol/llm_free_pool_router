[← Wiki index](README.md)

# 8. Evaluation method

*How a change to the harness gets decided. This is the design; the current
numbers are in [11. Evaluation status](11-eval-status.md).*

## 8.1 The premise

Changes to the harness — router config, system prompt, backend, agent loop —
are **evaluated, not argued**. A change that can't be shown to help doesn't
merge.

The unit of comparison is an **agent configuration**, never a single run.
Free-tier failover makes any one run noisy: which model served which step
depends on which accounts happened to be warm at that moment. So "this branch
is better" only means something as a distribution over repeated runs on a fixed
set of scenarios.

This is the point of the whole system: the harness stops being improved by
plausible reasoning and starts being improved by measurement.

## 8.2 Vocabulary

| Term | Meaning |
| --- | --- |
| **scenario** | A frozen codebase state — e.g. a project with a real bug in it. One commit in the scenario repo, named by a tag. |
| **task** | One prompt posed against a scenario, with a machine-checkable acceptance. One scenario carries several tasks. |
| **config** | One agent configuration: a commit of this repo plus any overrides. |
| **run** | One execution of (config × scenario × task × repetition). The atomic record. |
| **topic** | A branch in the scenario repo; its commits are that theme's scenarios. |
| **suite** | A named subset of tasks, so a targeted change needn't pay for the full set. |
| **verdict** | A run's graded result: automatic metrics + integrity checks + judge scores. |

## 8.3 Where things live

**Code with the code it measures; data on its own.**

- **This repo** — the agent under test, plus [evals/](../evals/): the runner,
  the metric definitions, the configurations, and the results. Metrics are code
  and evolve with the agent, so a metric change and the change it measures land
  in the same history.
- **`agent_evals`** — scenarios only. No harness, no results. A scenario is a
  commit whose tree is a real codebase state, which is the natural storage for a
  test case aimed at something that edits code.

Two consequences, both of which were nearly designed wrong:

- Configurations are branches of *this* repo, so a shared append-only results
  index would conflict on every merge. Results are therefore **one directory
  per run** with no index; a summary is a glob, not a query. Nothing to
  conflict on.
- Results answer LangSmith expiring: everything a verdict rests on is on disk
  ([7.1](07-observability.md#71-why-two)).

## 8.4 What a configuration is

A pinned commit plus overrides:

```yaml
# evals/configs/baseline.yaml
name: baseline
repo: ../free_coding_agent
ref: master                 # branch, tag or SHA
overrides:
  router_config: null       # a pool config, resolved in the repo (ROUTER_CONFIG)
  env:
    RECURSION_LIMIT: "150"
```

Resolution is `git worktree add --detach`, so a configuration runs from a clean
tree without disturbing your working copy — you can keep editing `master` while
a comparison runs. `router_config` is the one path resolved against the repo
rather than the worktree, which is why every configuration sets it to
`llm_router/config.yaml`: one pool, shared by every arm, instead of each arm
drawing from whatever its own commit pinned
([5.2](05-providers.md#52-config-schema)). The runner records the resolved
**SHA** plus a hash of the effective overrides as the fingerprint: `ref:
master` today and `ref: master` next week are different configurations, and the
records say so.

A configuration may also **pin a single model**, bypassing failover. That isn't
how the agent ships, but it's the lowest-variance way to attribute a change to
the prompt or loop rather than to which account happened to be warm
([8.6](#86-fair-comparison)).

## 8.5 The run lifecycle

Runs execute **serially**. Parallel runs contend for the same free-tier pool,
which both burns quota faster and makes each run's model mix depend on the
others — destroying comparability.

1. **Materialize** — `git archive` the scenario tag into a scratch workdir.
   Hash every file in the scenario's `immutable` manifest.
2. **Run** — `python -m agent.harness <workdir> < prompt` from the
   configuration's worktree, with `EVAL_TRACE_FILE` set. Kill at `timeout_s`.
3. **Capture** — stdout/stderr, wall time, exit status, `trace.jsonl`, and
   `diff.patch` (workdir vs untouched code, caches pruned).
4. **Verify** — copy the finished workdir somewhere pristine, overlay the
   withheld `evaluation/` directory, run the `fail_to_pass` and `pass_to_pass`
   sets. Record both ratios and the raw output.
5. **Integrity** — re-hash the immutable manifest. Any change marks the run
   `tampered`, which is a fail regardless of test outcome and is reported
   separately — it's a distinct failure mode, not the same as "got it wrong".
6. **Judge** — [10.4](10-metrics.md#104-the-judge). Skipped if a run with an
   identical diff hash was already judged.
7. **Record** — write `evals/results/runs/<run_id>/`, one self-contained
   directory.

`run_id` is `<scenario>_<task>_<config>_r<rep>_<UTC timestamp>`.

## 8.6 Fair comparison

The pool picks a different model per step depending on which accounts are warm
*at that moment*. Two threats follow. Both are design problems, not caveats.

**Confounding by pool state.** Configuration A run at 09:00 on a fresh pool and
B at 09:30 on an exhausted one are not comparable. Mitigations, strongest first:

1. **Interleave** — execute `A,B,A,B,…` per (task, rep), never all of A then
   all of B. Quota drift then hits both roughly equally. The runner does this.
2. **Report the model mix** per configuration. If the mixes differ materially,
   the comparison is flagged confounded rather than quietly reported as a win.
3. **Pinned-model mode** for changes where the pool isn't the variable (prompt,
   loop, tools). Much lower variance; validate the winner on the real pool
   afterwards.

**Sample size.** N repetitions × T tasks gives N×T trials. Default N=3 for a
quick read, N=5 when promoting. Report success rate with a Wilson interval and
treat overlapping intervals as **no difference**. Concretely: at N=5 × 6 tasks
(30 trials), differences under roughly 15 points are noise. Don't ship on them.

## 8.7 The promotion rule

> Promote a configuration over the baseline when **no task regresses by more
> than one trial**, *and* either success rate improves beyond interval overlap,
> or success rate holds flat while a secondary metric (`provider_calls`,
> `failover_bounces`, judge quality) improves materially.

Everything else is a draw. **A draw means keep the simpler configuration.**

These thresholds are a starting heuristic, not a measurement — recalibrate them
once a real baseline shows actual run-to-run variance.

## 8.8 Suites and selective running

Tasks carry `suite` and `tags`, and the runner filters:

```bash
python -m evals validate
python -m evals run --config baseline --reps 3
python -m evals run --config baseline --config my-change --reps 5   # interleaved
python -m evals run --config baseline --suite smoke --tags long-context
python -m evals show
```

- `smoke` — ~4 tasks, mixed categories: the cheapest thing that would catch a
  real regression. Run on any change.
- `full` — everything. Run before promoting.
- tag-scoped — a routing change runs `--tags long-context`; a prompt change runs
  `--tags ambiguous,trap`.

The tag→axis mapping is what makes selective running honest: a tag exists so
that *"I changed X, so I ran the tasks that exercise X"* is a checkable claim.

To exercise the runner without spending quota, drive the stub agent — but always
send it to a throwaway results directory, because a leaderboard mixing stubbed
and measured runs is worse than no leaderboard:

```bash
python -m evals run --config stub-fix --config stub-lost --reps 1 --results /tmp/selftest
```

## 8.9 Budget

A single task run is roughly 20–60 provider calls. `smoke` at 4 tasks × 3 reps ×
2 configurations ≈ 24 runs — several hundred to ~1500 calls, executed serially.
That is a meaningful fraction of a day's free-tier allowance, and runs *will*
hit cooldown waits. Plan a comparison as a background batch, not an interactive
loop.

Judging is the only paid component, and diff-hash caching keeps it to one call
per distinct patch.

## 8.10 Prior art, and why we still build

Public SWE-agent benchmarks are calibrated for frontier models on real
repositories. We run pooled free-tier 8B–70B models — a different capability
band. Hence: borrow their *design* freely, not their *task sets* as a primary
instrument.

| Project | Verdict | Rationale |
| --- | --- | --- |
| [SWE-bench](https://github.com/princeton-nlp/SWE-bench) | **borrow schema, defer tasks** | `FAIL_TO_PASS`/`PASS_TO_PASS` and the empty-patch/gold-patch validation gate are adopted wholesale. The tasks are L3: near-zero pass rates at first, so they can't discriminate between configurations yet, and they need per-repo Docker images. |
| [eth-sri/agentbench](https://github.com/eth-sri/agentbench) | **closest prior art** | Repo-level agent harness with `NONE`/`LLM`/`HUMAN` context settings, a generate → evaluate → analyze pipeline, and trace *and cost* analysis. Its context settings became our `context_mode`. |
| [langwatch/scenario](https://github.com/langwatch/scenario) | **adopt for one category** | Its user-simulator agent is the only credible way to auto-grade the **ambiguous** category — does the agent ask, or invent requirements? Its judge duplicates ours; use only the simulator. |
| [Galileo eval-engineer](https://github.com/Galileo-Agent-Labs/eval-engineer) | **steal the workflow, skip the platform** | Its "measured change retention" loop — diagnose from traces, bounded change, verify, keep only what evidence supports — is exactly the workflow here. But it's backed by hosted observability, reintroducing the dependency we removed by keeping traces local. |
| [RepoBench](https://github.com/Leolty/repobench) | **skip** | Next-line completion scored by Exact Match / CodeBLEU. No tools, no editing, no multi-turn. It grades a raw model, not a harness. |

**Containerized execution.** SWE-bench needs per-repo Docker images because
those repos need exact dependency versions. Our L0–L2 scenarios are authored as
dependency-free pure-Python trees, so the restricted `python`/`pytest` backend
is sufficient and Docker stays optional. Docker becomes mandatory only at L3 —
a further reason to sequence L3 last.

**One adapter, not N integrations.** Every external harness here drives an agent
through either an OpenAI-compatible endpoint or a thin adapter class. If several
are ever wanted, the cheap move is a small shim exposing the router as
`/v1/chat/completions` — then external harnesses test the *pool* while our own
scenarios test the *agent loop*, and neither needs bespoke wiring. Don't build
it until a specific harness is actually wanted; noted so it isn't re-derived.

---

**Previous:** [← 7. Observability](07-observability.md) · **Next:** [9. Scenarios →](09-scenarios.md)
