Record fresh runs, so a fix can be checked against evidence.

Launches `python -m evals run`. This is the expensive call in the loop:
it runs real agent sessions serially against the free-tier pool and can
take an hour. Name a `scenario` (or a `topic`) — a whole-suite batch is
a human's decision, not a tool call.

`ref` is the branch or SHA to measure. A fix the coding agent just
committed is on its branch, and a configuration pins `master`, so
verifying a fix means naming that branch here. Returns the run ids
recorded, which are what `check_issue` then reads.
