# Reviews

One post-mortem per run, written by the `trace-reviewer` subagent
(`.claude/agents/trace-reviewer.md`), named `<run_id>.md`.

A review answers what the metrics cannot: **where a run turned, and what context
it had at that point.** It carries no score — the verdict is in the run's
`run.json`, computed from hidden tests.

**The six headings are fixed**, so reviews can be read against each other. The
evidence a review has to work from is `trace.json` when tracing was on and
`trace.jsonl` plus `stderr.log` otherwise, and "what it had there" means what was
still in the history after compaction — and what the agent never went and
fetched.

Reviews of runs from the deleted narrow-role arm (`session`, `context-and-gate`,
`harness-v*`) are still here and still readable. They quote `journal.jsonl`,
`steps/NN-<role>.md` and `rationale.md`, which no run produces any more
([The arm that was deleted](../../../docs/agents/code.md#the-arm-that-was-deleted)).

What to trust in a run, and what not to, is in
[Reading a recorded run](../../../docs/evaluation/reading-runs.md): a timed-out
run has no `trace.json`, and `run.json`'s trace-derived counts are zero on any
run without a `trace.jsonl`.

These are the open-coding notes the J2 batch analysis works from
(`.claude/skills/j2-error-analysis/`). J2 counts and ranks; a review explains one
run. Both are kept forever on disk, for the same reason: a diagnosis that quietly
stops being true between two runs is itself a finding. Neither is committed — like
the runs they read, they are artifacts. What reaches the repo is a report in
`../reports/`.

See [Observability](../../../docs/evaluation/observability.md#the-record-one-run-tree) for the run tree, and
`docs/design/long-run-harness.md#9-reading-one-session-back-the-post-mortem` for
the post-mortem's original argument.
