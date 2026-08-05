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
| `baseline` | `master` | — | `scenario/http-headers/retry-after-case` L0, n=2 | 1 pass, 1 crash | reference point |
| `adhoc-harness` | `harness/adhoc-router` | Replaces the deep-agents loop with narrow per-role agents over a shared blackboard | — | **not run yet** | candidate; see below |

### adhoc-harness, pre-measurement note

Built and smoke-tested live, **not** compared against baseline yet. One ad-hoc
run on a seeded one-line bug passed using 9 provider calls / 3,369 input tokens
across 2 Groq models, with no Gemini call. That is n=1 on a different scenario
from the baseline row above, so it is evidence the thing works, not evidence it
is better.

The comparison that would settle it:

```bash
python -m evals run --config baseline --config adhoc-harness --reps 5
```

Read `tokens_in` and `models_used` as outcomes rather than confounds here: the
change is *intended* to shift work back onto the cheap Groq models, which
[docs/08-evaluation-method.md](../docs/08-evaluation-method.md#86-fair-comparison)
would otherwise flag as a confounded mix.

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
