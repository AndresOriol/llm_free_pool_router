# Evaluating an agent run

The contract for scoring one free-agent run on one brief. The point is to turn
"did the agent do a good job" into a few fields you can fill from the run log /
LangSmith trace + the brief's acceptance check, so runs are comparable over
time and regressions are visible.

## The objective function

A brief's **Acceptance** section is the ground truth: a command (usually a
`pytest` invocation) that must pass. Everything else is diagnostics. A brief
without a machine-checkable acceptance can't be evaluated — don't run one.

## Nondeterminism

The pool picks a different model per step via failover, so the *same brief
behaves differently each run*. A single pass/fail is noise. Run each brief
**N times (default 3)** from a clean git state and look at the distribution
(e.g. "2/3 passed"), not one outcome.

## Per-run scorecard

Fill one row per run. Source column says where the number comes from.

| Field | Meaning | Source |
| --- | --- | --- |
| `outcome` | `pass` / `fail` / `crash` — did the acceptance command pass? `crash` = agent errored or hit the recursion limit without finishing | run acceptance cmd after the run |
| `steps` | agent loop iterations (tool-call rounds) | trace / log |
| `provider_calls` | total LLM calls incl. failover retries | log (`Routing to ...` lines) |
| `failover_bounces` | transient failures before a step succeeded — proxy for wasted quota | log (`transient failure` lines) |
| `bad_tool_calls` | invalid tool name, failed `edit_file`, malformed args | log (ToolMessage `Error:` lines) |
| `models_used` | distinct models that served a step | log |
| `wall_time_s` | end-to-end run time | wall clock |
| `notes` | qualitative: scope violation, over-engineering, misread brief, good recovery | human review (optionally `/code-review`) |

## What the fields map to (evaluation axes)

- **Task success** → `outcome`. The only axis that gates "did it work".
- **Tool competence** → `bad_tool_calls`. High on small models; a known cost.
- **Autonomy / loop-closing** → did the agent run the acceptance test *itself*
  (via the `execute` tool) and react to the result, or did it stop at "I wrote
  the code"? Read from the trace. Requires the backend to support `execute`.
- **Efficiency** → `steps`, `provider_calls`, `failover_bounces`, `wall_time_s`.
  A run that passes but burns the whole pool is a weak pass.
- **Robustness to failover** → `models_used` vs `outcome`: did switching model
  mid-task derail it?
- **Instruction adherence / quality** → `notes`, from diff review.

## Not automated yet

Trace parsing and an LLM-judge for the quality axis are deliberately deferred —
score by hand until the manual pass hurts, then automate the fields above from
the LangSmith trace. Keep this list short; a metric nobody acts on is noise.
