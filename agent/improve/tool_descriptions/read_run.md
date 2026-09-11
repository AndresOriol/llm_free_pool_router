Read one recorded run, one section at a time.

Sections: `summary` (verdict, cost, tool mix, the longest silences
between provider calls), `turns` (the spine of the run tree: one line
per model call with its context size), `turn:<n>` (that call's context
tail, what it answered and what its tools returned), `events` (the flat
tool/model timeline), `diff`, `verify` (the hidden tests' output),
`account` (what the agent itself claimed -- never evidence on its own).
