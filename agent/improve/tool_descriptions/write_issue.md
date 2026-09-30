Create or amend one issue in the ledger. Returns the issue.

Call it only for a claim you have checked against the fullest record of
the runs you cite and against the lever as it stands now. If something you
already read contradicts the claim, or the lever already holds the fix,
do not open the issue.

An issue is a *pattern*, so `evidence` (space- or comma-separated run
ids) should name at least two runs; one instance is an anecdote. `lever`
names the file or knob a fix would land in — an issue whose fix is "be
smarter" is not an issue. `severity` is low|medium|high.

`signature` is JSON and is what later closes or reopens this, so write
it to match the failure and nothing else. Keys: `kind` ("eval"/"live"),
`where` (run.json fields, values either literal or like "> 200000"),
`grep` (a regex over the trace). Example:
{"where": {"failure_class": "stopping"}, "grep": "GraphRecursionError"}
