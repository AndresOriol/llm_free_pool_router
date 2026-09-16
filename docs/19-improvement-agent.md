[← Wiki index](README.md)

# 19. The improvement agent

*The agent whose project is the other agents. What it reads, what it may not do,
and the one rule that decides whether a fix worked.*

## 19.1 The problem: the loop was a person

Everything needed to improve this harness already exists, and a human is the
part that moves between the pieces. Runs are recorded with verdicts, metrics and
traces ([10](10-metrics.md)); there is a per-run post-mortem method
(`.claude/agents/trace-reviewer.md`) and a batch error-analysis method
(`.claude/skills/j2-error-analysis`); there is a coding agent that can change the
harness ([6](06-agent.md)); and there is a runner that will measure whether the
change helped ([8](08-evaluation-method.md)).

What there is not is anything that goes round. Somebody opens the runs, notices
the same thing keeps happening, works out why, writes a brief, watches the agent
make the change, and then remembers weeks later to check whether it stopped.
The two expensive halves of that — the noticing and the remembering — are the
halves nobody does.

The reference for closing it is [LangSmith
Engine](https://docs.langchain.com/langsmith/engine), which describes the same
loop over production traces: detect a recurring issue, diagnose it against the
connected source, open a pull request with the fix, then track matching traces
and reopen the issue if the pattern comes back. `agent/improve/` is that loop on
this project's own evidence, with this project's constraints on it.

## 19.2 The loop

Five stages. Each is a tool, and each stage's output is what the next one reads.

| Stage | Tool | What it produces |
| --- | --- | --- |
| Detect | `find_runs` | which runs show a pattern, and how many |
| Diagnose | `read_run` | one run, one bounded section at a time |
| Name | `write_issue` | a ledger entry carrying a **signature** |
| Fix | `delegate_fix` | runs `python -m agent.code`, recorded on the issue |
| Check | `run_evals`, `check_issue` | fresh runs, then close or reopen |
| Grow the instrument | `draft_scenario` | a failed run becomes an eval case |

Seven tools, and the count is a budget rather than a preference: tool schemas
are 91% of what a step of this loop spends
([6.4](06-agent.md#64-why-it-is-shaped-this-way)). `check_issue` doubles as the
ledger listing so that reading what is already known does not cost an eighth.

`draft_scenario` is the one that is not a stage of the fixing loop, and it earns
its schema by raising the ceiling on every other stage. The eval set bounds what
any measurement here can claim — including this agent's claims about its own
fixes — and a run the agent failed is a description of a test it would fail
([20](20-probes.md) is the small end of the same argument). Building it is a
second binding of the coding agent, `scenarios`, jailed to the eval repository
rather than this one, registered only when that repository is really there.

The standing job is the loop's second half, not its first. `python -m
agent.improve .` with nothing on stdin runs *work the ledger*: re-check
everything that is not closed, and only then go looking for something new.

## 19.3 It cannot change the harness, and that is the point

The agent that diagnoses is not the agent that changes the code.

[`ReadOnlyMiddleware`](../agent/improve/agent.py) refuses `write_file`, `edit_file`
and their kin as a `ToolMessage`, so the model can read the refusal and correct
from it in one step rather than losing the run to an exception.

**That is now the whole of it, and it only covers the tools.** This agent runs
on `LocalShellBackend` like the coding agent, so `execute` is the host shell and
a `python -c` away from writing any file the middleware refuses
([6.2.1](06-agent.md#621-why-the-restrictions-went)). Not changing the harness
is a discipline this agent is asked to keep — stated in its prompt, enforced
nowhere. Read its diffs, not its summary.

The reason is not containment, it is reviewability. A diff written by the agent
that diagnosed it is the only account of itself; a diff written by a different
agent, against a brief that was written down first, can be read against that
brief. The ledger holds the diagnosis, the brief, the task id and the outcome,
so the sequence is checkable by a human who was not there.

The ledger itself is written through Python rather than through the filesystem
tools, so nothing needs a hole in the rule.

## 19.4 What counts as evidence

Two kinds of record, and the difference is whether anyone knows the right
answer ([records.py](../agent/improve/records.py)):

- **eval** — `evals/results/runs/<run_id>/`, with a `run.json`. Hidden tests
  decided the outcome, so the verdict is ground truth.
- **live** — an unattended or served run's directory (`AGENT_TRACE_FILE`,
  `SERVE_RECORD_DIR`), with a trace and no verdict. This is the closest thing
  here to Engine's production traces, and a finding from one rests entirely on
  reading the trajectory.

Both are searched, because the failure that matters most is the one that only
happens in the wild. `kind` travels with every row, because the thing that must
never happen is quoting a live record as though a test had passed on it.

### 19.4.1 The traps that are already known

The prompt carries five, each of which has produced a wrong conclusion in this
repository before:

- **A trace is older than the code.** Every recorded run was made against a
  commit, and the file you are about to blame has moved since. This is the one
  that ruined the first live pass ([19.9](#199-what-the-first-live-pass-showed)),
  and it is now enforced as well as stated: `delegate_fix` refuses an issue whose
  evidence all predates its lever's last change.
- **The agent's own closing message is not evidence** — including the delegate's. Check it against the diff
  and the hidden tests. A claimed change absent from the diff is the headline
  finding, because the whole review model assumes it does not happen.
- **A zero is ambiguous.** Every trace-derived count comes from `trace.jsonl`;
  on a run that never wrote one they are zero by construction, and reading that
  as cheapness inverts the comparison this project turns on
  ([6.1.1](06-agent.md#611-the-arm-that-was-deleted)).
- **`stopping` is four failures wearing one name** — a loop that never settled,
  a run that ran out of clock, one provider call that hung, a starved pool.
  `read_run`'s summary computes the longest silences between provider calls,
  which is what separates them.
- **Pass rates at these sample sizes are noise**
  ([6.4.2](06-agent.md#642-the-pass-column-is-noise)). `tokens_in` replicates;
  the pass column does not.

A fifth was found while building this and is fixed rather than documented as a
caution: a 4 MB read cap truncated a real 4.7 MB `trace.json`, which then failed
to parse and was reported as *"no trace.json in this record"* — the exact
sentence that tells a reader the run was never traced. Trace files are streamed
now, and "would not parse" and "was never written" are different messages.

## 19.5 The signature, and why an issue can close itself

An issue carries a declarative predicate over the record:

```json
{"kind": "eval",
 "where": {"failure_class": "stopping", "tokens_in": "> 200000"},
 "grep": "GraphRecursionError"}
```

`where` clauses read `run.json`; `grep` is a regex streamed over the trace, the
router's narration and the agent's own account. All must hold.

Three properties are load-bearing:

- **It is data, not code.** A pass months later can replay it without having
  read the trace that produced it — which is the whole reason the ledger is
  worth keeping.
- **A clause on a field the record lacks never matches.** That is what stops a
  signature written about eval verdicts from silently matching a live run, which
  has no `run.json` at all.
- **An empty signature matches nothing.** It would otherwise read as "this
  failure is everywhere".

This is the analogue of Engine's proposed evaluator: the thing that makes the
same failure unable to recur silently after a fix ships.

## 19.6 The rule that decides whether a fix worked

An issue closes when the signature stops matching and the combined solved count
over the train and holdout splits has not regressed, with at least one post-fix
run on each split. Not when the coding agent says it is done, and not when the
improvement agent finds the diagnosis convincing.

`check_issue` defaults its boundary to when the fix was delegated, which is the
only date that answers whether it stopped happening.

| After the fix | Result |
| --- | --- |
| The signature still matches | not fixed; reopen if it was closed (unchanged) |
| Nothing matches, but no run on the holdout split | status unchanged, unverified on the holdout and names the `run_evals` call |
| Nothing matches, both splits have runs, combined solved count below baseline | status unchanged, reported as a regression, naming which split lost |
| Nothing matches, both splits have runs, but no baseline was recorded | status unchanged; the comparison could not be made |
| Nothing matches, both splits have runs, combined solved count holds or improves | **closed** |

An issue that closed because nobody looked — or because only the half it was
diagnosed from was looked at — is worse than one still open, because it is a
silent claim of a fix resting on no measurement. So a check with nothing to
check, with nothing on the holdout, or with no baseline to compare against,
leaves the status alone and reports the fix as unverified.

The two real issues (`invariant-guard-over-declines-explicit-doc-updates` and
`recursion-limit-crash-on-long-running-sessions`) were diagnosed from exactly two
runs of one scenario each, proving the old gate was too weak.

A pass count at these sample sizes is weak evidence and the gate is a floor,
rather than a proof (see [6.4.2](06-agent.md#642-the-pass-column-is-noise)).


## 19.7 Making new evidence costs real quota

`run_evals` launches `python -m evals run` as a subprocess — real agent sessions,
serially, against the free-tier pool, for up to an hour per call
(`IMPROVE_EVAL_TIMEOUT`). It is the only agent here that starts other agents as
subprocesses rather than in-process, because the eval runner materialises a
pinned worktree per configuration and that is the point of it
([8.4](08-evaluation-method.md#84-what-a-configuration-is)). Re-running the
holdout costs roughly a day of free-tier quota (~20 provider calls a run, ~9 runs
a day on the flash tier), which is payable once per fix.

Two guards, both on the tool rather than in the prompt: it refuses a batch with
no scenario or topic named, and it caps reps at three. A whole-suite batch is a
human's decision.

The verification that would otherwise be silently wrong is the *ref*. A
configuration pins `master`; a fix the coding agent just committed is on its own
branch. `python -m evals run` now takes `--ref` to resolve every configuration
against a named branch or SHA
([evals/agent_config.py](../evals/agent_config.py)), and the resolved SHA lands
in each `run.json` as it always did, so a verification batch can never be
mistaken for a batch of the pinned configuration. Without that flag a
verification pass would measure the unfixed code and conclude the fix failed.

## 19.8 How its judgement is measured

A full pass costs twenty minutes and one diagnosis, which makes the agent's
judgement the most expensive thing here to have an opinion about — and at the
time of writing the entire evidence base is one pass that got the answer wrong.

`python -m evals probes --agent improve` is the cheap instrument: the real agent,
one situation, stopped at its first decision, one model call
([20](20-probes.md)). The four that exist ask whether it reads the ledger before
the traces, whether it delegates before diagnosing, whether it tries to edit the
harness it is forbidden to touch, and whether it reaches for a `python` it does
not have. None of them can say a pass was any good. All of them can say it
started wrong, which is where every recorded failure of this agent so far has
begun.

## 19.9 What it costs, and what is unmeasured

**Unmeasured.** Like delegation itself
([16.6](16-delegation.md#166-what-this-costs-and-what-is-unmeasured)), this
is a configuration nobody has run against a baseline. What can be said now:

- One pass reads traces, which are large. Every section of `read_run` is
  bounded and searching streams, so a pass does not scale with the size of the
  trace directory — but it does scale with how many runs the model chooses to
  open.
- One `delegate_fix` is a whole coding session, on the same pool, run as a
  command: it does not share this pass's cooldown, but it does land in this
  pass's trace ([16.4](16-delegation.md#164-why-a-subprocess-costs-something-real)).
  A pass's `tokens_in` therefore includes the fix.
- One `run_evals` is a batch. It is by far the most expensive thing in the loop
  and the only one that spends hours.

`IMPROVE_FIX=0` removes the `code` peer and leaves a diagnose-only pass, which
is the arm to compare against when asking whether the delegation is worth what it
spends — the same arrangement `AGENT_PEERS=` provides for the coding agent.

## 19.10 What the first live pass showed

One pass, 2026-09-08, over 105 recorded runs (103 eval, 2 live). 21½ minutes,
120 turns, 125 tool calls, 52 reroutes across two Gemini members. It went round
the whole loop — opened one issue, delegated it, and checked it — and the
outcome was wrong in a way worth keeping.

**It diagnosed a failure that had already been fixed.** It found two real
`GraphRecursionError` crashes, cited them correctly, wrote a well-formed
signature, named `agent/code/session.py` as the lever, and described a 120-step
limit that had been 400 since the day before the runs it was reading. The
evidence was real; the conclusion was a day stale. It never asked when the lever
last changed.

**Then the coding agent said it had made the change.** The delegated session
altered no code file at all — the work was already done — and appended a
`NOTES.md` entry stating it had raised the limit, added the reserve and replaced
`invoke` with `_drain`. The improvement agent repeated that in its own closing
summary. This is the failure both this page and the `trace-reviewer` method name
as the headline finding, and it was produced on the first try.

**What held.** `check_issue` ran after the delegation, found `considered: 0`,
and left the issue `fixing` — it did not close on the coding agent's word, which
is the one rule the design rests on ([19.6](#196-the-rule-that-decides-whether-a-fix-worked)).
`ReadOnlyMiddleware` refused two `edit_file` calls. No quota was spent on a
batch, because the agent never reached `run_evals`.

Three changes came out of it, all in this repo:

1. **`delegate_fix` refuses an issue when no run showing it ever ran the
   current lever.** `write_issue` only warns — a file may have moved for an
   unrelated reason and only a reader can tell — but a delegation costs a whole
   coding session, and at that price the rule is that you may not ask for a
   change to a file when the failure has never been observed against the current
   state of it. The way through is `run_evals`, then citing the fresh run.

   **The test is which commit a run exercised, not when it ran.** An eval run
   materialises a pinned worktree and records the SHA
   ([8.4](08-evaluation-method.md#84-what-a-configuration-is)), so a run started
   after a fix can still be running the code from before it; the check asks
   whether the lever's last-changing commit is an ancestor of the run's
   `config_sha`. The first version of it compared timestamps and reached the
   right verdict on this very case by luck — `git` reports a commit date in the
   committer's local offset, and `10:20:18+02:00` sorts after a UTC stamp an
   hour later in real time. A live record has no pinned SHA and falls back to
   the clock, which is a guess and is documented as one.

   The pass had the facts: it ran `git log --oneline 72748b4..master` and read
   the answer. It did not join them to its own diagnosis, which is the argument
   for making this a refusal rather than another sentence in the prompt.
2. **The prompt now says the delegate's report is not evidence either**, and
   `delegate_fix` returns that instruction with the task, naming the commands
   that settle it.
3. **The shared `## Running commands` section no longer advertises a Python escape hatch to an
   agent without Python.** That section is shared with the coding agent, and it
   told this one to reach for `python -c` when it needed anything beyond `git`:
   10 of its 28 `execute` calls were refused programs, 8% of the run spent
   discovering a boundary the prompt had misdescribed. It is the exact failure
   this project already names — a description promising what the backend will
   not do. Both halves are gone now: there is no allowlist to misdescribe, and
   the shared section that described one was deleted with it.

Two findings that are not about this agent are in the ledger rather than fixed:
the coding agent's false account, and the fact that a delegated session leaves
no record this loop can read (an A2A task JSON is not a run record), which is
why the false account had to be caught by hand.

## 19.11 What is deliberately not built

- **No pull request.** The coding agent commits on a branch and is asked not to
  push. Opening a PR is a remote operation and this project has decided the
  agent does not do those; the branch and the ledger entry are what a human
  reviews. Since the git allowlist went
  ([6.2.1](06-agent.md#621-why-the-restrictions-went)) this is prompt and
  convention, not enforcement.
- **No severity model, no scoring, no dashboard.** The ledger is a directory of
  JSON files and `check_issue` prints it. A panel would be a second place for
  the status to be wrong.
- **No annotation queue and no human feedback signal.** Engine treats run
  feedback as a high-priority input; here the equivalent is the hidden tests'
  verdict, which is stronger and already in `run.json`. The place a human writes
  is a report's `## Comments` section and `NOTES.md`, both of which this agent
  reads as ordinary files.
- **It does not grade prose.** That is J1's job, and J1 does not exist.

## 19.12 Running it

```bash
python -m agent.improve .                          # work the ledger
echo "The last batch failed mostly as stopping. Split that class." \
  | python -m agent.improve .
```

It refuses to start when the project has no recorded runs, and names the command
that would fix that. Over HTTP it is `improve` alongside `code` and `explore`
([18](18-serving.md)); its card is always served, because what it needs is runs
in the *bound workspace* and a workspace arrives with the task — so the probe
happens in the handler, one step later, and a workspace with no runs is a
rejected task rather than a missing agent.

The ledger is at `evals/results/issues/`, beside the runs it indexes.
