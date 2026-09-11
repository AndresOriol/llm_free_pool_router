Find recorded runs, optionally the ones whose trace matches a regex.

Filters: kind `eval` (has a verdict from hidden tests) or `live` (an
unattended or served run, no verdict); `since` a UTC prefix like
`2026-09-07`; `config` and `outcome` read from run.json. `pattern` is a
Python regex searched across the run's trace, router narration and final
message -- the way to ask "how many runs did X". Returns one line per
run, newest first, with matching lines under each hit.
