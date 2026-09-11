Describe an eval scenario drawn from a run, and optionally build it.

A run the agent failed is a description of a test it would fail. The set
has five scenarios and cannot currently tell one configuration from
another, so a good scenario is worth more than most fixes — including
to you, since every claim you make about a fix rests on it.

`prompt` is the exact text the agent would be given. `fail_to_pass` is
what must go from failing to passing; without it the scenario decides
nothing and this refuses. `traps` names the answers that satisfy the
task's letter and miss it — a scenario with none is not worth building.
`category` is bugfix|feature|refactor|tests|ambiguous|trap, `difficulty`
L0..L3. `source_run` is read for provenance, so the brief cannot claim a
failure the run did not have.

Pass `build="yes"` to hand the finished draft to a coding session in the
scenario repository. That costs a whole session, so draft first, read it
back, and build when it is right.
