# Reviews

One post-mortem per run, written by the `trace-reviewer` subagent
(`.claude/agents/trace-reviewer.md`), named `<run_id>.md`.

A review answers what the metrics cannot: **where a run turned, and what context
it had at that point.** It carries no score — the verdict is in the run's
`run.json`, computed from hidden tests.

**The same six headings serve both arms**, so a `session` review and a
`deepagents` review can be read against each other. What differs is the evidence
and therefore the question:

| Arm | Evidence | "What it had there" means |
| --- | --- | --- |
| Narrow roles (`session`, `context-and-gate`, `harness-v*`) | `journal.jsonl`, `steps/NN-<role>.md`, `rationale.md`, `trace.jsonl` | What the orchestrator's brief pushed down to that role |
| Conversational (`deepagents`) | `trace.json` when tracing is on, otherwise `stderr.log` | What was still in the history after compaction, and what the agent never went and fetched |

Two things to know before reading a `deepagents` review. `trace.json` is fetched
from LangSmith *after* the run, so a timed-out run has none and the review works
from the router's narration in `stderr.log`. And `run.json`'s trace-derived
metrics — `provider_calls`, `tokens_in`, `models_used`, `steps` — are zero on that
arm by construction, because `evals/metrics.py` reads the `trace.jsonl` only the
narrow arm writes. Reviews count from `stderr.log` and say that they did.

These are the open-coding notes the J2 batch analysis works from
(`.claude/skills/j2-error-analysis/`). J2 counts and ranks; a review explains one
run. Both are kept forever on disk, for the same reason: a diagnosis that quietly
stops being true between two runs is itself a finding. Neither is committed — like
the runs they read, they are artifacts. What reaches the repo is a report in
`../reports/`.

See `docs/07-observability.md#77-the-record-one-run-tree` for the run tree, and
`docs/design/long-run-harness.md#9-reading-one-session-back-the-post-mortem` for
the post-mortem's original argument.
