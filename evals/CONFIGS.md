# Configuration ledger

Every agent configuration tried, with the verdict and the reason. Losing
changes are recorded too — knowing what didn't work is most of the value of
keeping the data at all, and it's what stops the same idea being re-tried every
few months.

Promotion rule (see [8.7](../docs/08-evaluation-method.md#87-the-promotion-rule)):
promote when no task regresses by more than one trial **and** either success
rate improves beyond interval overlap, or success rate holds flat while a
secondary metric improves materially. Anything else is a draw, and a draw keeps
the simpler configuration.

| Config | Ref / SHA | Change | Suite | Result | Verdict |
| --- | --- | --- | --- | --- | --- |
| `baseline` | `master` | — | L0 `retry-after-case`, n=3 | 3/3 | reference point |
| `adhoc-harness` | `harness/adhoc-router` | Narrow per-role agents over a shared blackboard | L0, n=6 | 4/6, 15k tok | **draw** — keep measuring |
| `harness-v3-merged` | `harness/adhoc-router` | One `investigate` role replaces locate + inspect | L0, n=6 | 4/6, 10.5k tok | **draw** — its 3/3 did not replicate |
| `harness-v6-guarded` | `harness/adhoc-router` | Forced summary + no-note guard on edit | L0, n=3 | 2/3, **5.8k tok** | cheapest by far; undecided |
| `harness-v5-lean` | `harness/adhoc-router` | Seeded files + edit retry + two-tool locate | L0, n=3 | 1/3 | **dropped** |
| `harness-v2-seeded` | `harness/adhoc-router` | Glob the file list, skip `locate` | L0, n=3 | 0/3 | **dropped** |
| `harness-v7-orchestrated` | `harness/adhoc-router` | Hub and spoke; execution as an agent | L0, n=3 | 0/3, most calls | **dropped for this task shape** |

### The harness family, 2026-08-06

Full write-up in
[docs/06-agent.md](../docs/06-agent.md#614-architecture-variants-tried). Three
things worth carrying forward:

**No promotion.** Nothing beat baseline on the gating axis, and nothing is
distinguishable from anything else. `harness-v3-merged` scored 3/3 in one batch
and 1/3 in the next on an identical configuration — direct evidence that one L0
task at n=3 sits inside the noise floor, exactly as
[docs/08-evaluation-method.md](../docs/08-evaluation-method.md#86-fair-comparison)
predicts. Any ranking read off these pass rates would be invented.

**The cost result is real.** Token and call counts replicated across every rep
and batch, with between-configuration spread far exceeding within-configuration
variance: `harness-v6-guarded` runs the task on 7.3 calls / 5,756 input tokens
against baseline's 20.0 / 226,854. A 39× reduction, on a pool where tokens are
the binding constraint.

**Every failure is `reasoning`** — 12 of 13, with one `stopping` and zero
`retrieval` or `tooling`. Every variant found the file, edited it, and ran the
tests; they got the fix conceptually wrong. Architecture changes cannot move
that number, which is the strongest available argument for spending the next
effort on scenarios and model tiering rather than on more topologies.

**Do not re-run these on L0.** They are exhausted as a comparison. The next
useful measurement is the same set against a scenario that can discriminate.

### baseline, first measurement (2026-07-29)

Two reps of one L0 task. Not a rate — n=2 establishes the pipeline works and
gives a first look at variance, nothing more.

| | rep 1 | rep 2 |
| --- | --- | --- |
| outcome | pass | crash (`stopping`) |
| steps | 6 | 5 |
| provider calls | 11 | 10 |
| failover bounces | 4 | 8 |
| tokens in | 89,260 | 55,126 |
| distinct models | 5 | 7 |
| wall time | 21.6s | 12.7s |

Two observations already worth acting on:

1. **Rep 2 crashed on a dead model, not on the task.** Groq returns
   `404 model_not_found` for `meta-llama/llama-4-scout-17b-16e-instruct`. A 404
   is not in the router's transient set, so it propagates and kills the whole
   run. A decommissioned model in the pool should be disabled permanently and
   skipped, the same way a rate-limited one is skipped temporarily — otherwise
   one stale config entry can end an unattended run.
2. **A one-line fix costs 5–7 distinct models and ~90k input tokens.** The
   failover machinery works, but the pool is being walked hard for a trivial
   task. Worth understanding before reading anything into efficiency numbers.

## Stub configurations

`stub-*` are not real configurations. They drive `evals/fake_agent.py` to
exercise the runner's own paths (pass, each failure class, tampering) without
spending free-tier quota. Always run them with `--results` pointing somewhere
throwaway so they never enter the real record.

```bash
python -m evals run --config stub-fix --config stub-noop --config stub-badedit \
  --config stub-lost --config stub-tamper --reps 1 --results /tmp/selftest
```
