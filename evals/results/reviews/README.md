# Reviews

One post-mortem per run, written by the `trace-reviewer` subagent
(`.claude/agents/trace-reviewer.md`), named `<run_id>.md`.

A review answers what the metrics cannot: **where a run turned, and what
context it had at that point.** It carries no score — the verdict is in the
run's `run.json`, computed from hidden tests.

These are the open-coding notes the J2 batch analysis works from
(`.claude/skills/j2-error-analysis/`). J2 counts and ranks; a review explains
one run. Both are kept forever on disk, for the same reason: a diagnosis that
quietly stops being true between two runs is itself a finding. Neither is
committed — like the runs they read, they are artifacts. What reaches the repo
is a report in `../reports/`.

See `docs/design/long-run-harness.md#9-reading-one-session-back-the-post-mortem`.
