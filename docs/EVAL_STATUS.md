# Agent evaluation — status

Where the evaluation of the coding agent currently stands: what's built, what
it measures, what the numbers say today, and what's next. The *reasoning*
behind the design — metrics definitions, fairness rules, the promotion rule —
is in [EVAL.md](EVAL.md); this file is the running state.

**Last updated: 2026-07-29.** Update it when a phase lands, a scenario is
added, or a comparison is decided. A status doc nobody updates is worse than
none.

## The approach in one paragraph

Changes to the harness are decided by measurement, not argument. A change is an
**agent configuration** (a commit of this repo plus overrides); it's run
against **scenarios** (frozen codebase states with something wrong in them,
stored in the `agent_evals` repo) and scored on hidden tests it never sees.
Because free-tier failover makes any single run noisy, nothing is concluded
from one run — configurations are compared over repetitions, interleaved so
neither gets the fresher pool.

## What's built

| Phase | State | What it covers |
| --- | --- | --- |
| **P0** trace capture | **done** | `EVAL_TRACE_FILE` makes the agent append one JSON object per LLM/tool event ([agent/trace.py](../agent/trace.py)). Every automatic metric is a sum over that file. |
| **P1** runner | **done** | [evals/](../evals/): materialize → run → verify → integrity → record, plus `validate` and `show`. Metrics automatic, including the failure taxonomy. |
| **P2** judge | not started | `claude -p` with a pinned rubric and diff-hash cache. Quality scoring is manual until then. |
| **P3** compare/report | not started | Leaderboard and written comparisons. `show` covers the basics today. |
| **P4** scenario library | **1 of ~15** | Only one L0 scenario exists. This is the main gap. |
| **P5** SWE-bench L3 | not started | Needs Docker. Deliberately last. |

## Where things are

| | |
| --- | --- |
| Harness | this repo, [evals/](../evals/) |
| Configurations | [evals/configs/](../evals/configs/) |
| Results (durable, committed) | `evals/results/runs/<run_id>/` |
| Ledger of configurations tried | [evals/CONFIGS.md](../evals/CONFIGS.md) |
| Scenarios | `agent_evals` repo, sibling of this one |

## How to run it

```bash
python -m evals validate
```

Checks every scenario is still well-formed: against untouched code the
`fail_to_pass` tests must fail and the `pass_to_pass` tests must pass, and the
reference patch must make all of them pass. Runs automatically before any
comparison — a scenario that drifts would otherwise look like every
configuration regressing at once.

```bash
python -m evals run --config baseline --reps 3
python -m evals run --config baseline --config my-change --reps 5   # interleaved
python -m evals run --config baseline --suite smoke --tags long-context
python -m evals show
```

Runs are serial and interleaved across configurations on purpose: parallel runs
contend for the same free-tier pool, and running all of A before all of B hands
one configuration a fresh pool and the other an exhausted one.

To exercise the runner without spending quota, drive the stub agent — but
always send it to a throwaway results dir, because a leaderboard mixing stubbed
and measured runs is worse than no leaderboard:

```bash
python -m evals run --config stub-fix --config stub-lost --reps 1 --results /tmp/selftest
```

## Where the numbers stand

**One scenario, one configuration, n=2.** This establishes the pipeline works
end to end. It is not yet a measurement of anything about the agent — see
[EVAL.md](EVAL.md#fair-comparison) for why nothing should be concluded at this
sample size.

| config | runs | pass | rate | calls | bounces | failure classes |
| --- | --- | --- | --- | --- | --- | --- |
| `baseline` | 2 | 1 | 50% | 10.5 | 6.5 | stopping=1 |

Per-run detail in [evals/CONFIGS.md](../evals/CONFIGS.md); raw evidence in
`evals/results/`.

Two observations, both n=2 and both about the *harness* rather than the agent:

1. **The one failure was infrastructure, not capability.** See blockers below.
2. **A one-line fix costs ~10 provider calls across 5–7 distinct models and
   tens of thousands of input tokens.** Failover works, but the pool is walked
   hard for trivial work. Worth understanding before reading anything into
   efficiency numbers.

## Blockers

| Issue | Impact | State |
| --- | --- | --- |
| Groq returns `404 model_not_found` for `llama-4-scout`; 404 isn't in the router's transient set, so it propagates and kills the run | ~50% of runs crash for a reason unrelated to the task, which makes any pass rate meaningless | flagged as separate work; fix on a branch and measure it |
| Only one scenario, at L0 | Nothing to compare configurations on. L0 is a canary, not a comparison instrument | P4 |
| `agent_evals` local history has diverged from its GitHub remote after the restructure | Scenarios aren't backed up | needs a force-push decision |

## What to do next

1. **Fix the dead-model crash.** Nothing measured is trustworthy while half the
   runs die on it. It's a candidate change like any other: branch, add a
   configuration, measure against baseline.
2. **Author scenarios** — L1 and L2, across the categories in
   [EVAL.md](EVAL.md#scenario-categories). The comparison instrument is
   whatever currently lands between roughly 20% and 80% pass rate; L0 alone
   can't distinguish two configurations.
3. **Re-baseline** at n=5 once those two are done, and record it in the ledger.
4. **P2, the judge** — worth building only once there are enough scenarios that
   reading diffs by hand hurts.

## How this changes day-to-day work

A harness change is no longer merged because it sounds right. Branch it, add a
configuration, run the suite that covers it interleaved against baseline, apply
the promotion rule in [EVAL.md](EVAL.md#fair-comparison), and record the verdict
in [evals/CONFIGS.md](../evals/CONFIGS.md) — including for changes that lost,
since knowing what didn't work is most of the value of keeping the data.
