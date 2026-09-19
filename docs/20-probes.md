[← Wiki index](README.md)

# 20. Probes — the small tests

*One agent, one situation, one decision. What a probe can prove, what it cannot,
and why the files rather than the dataset are the source of truth.*

## 20.1 The problem: one test, and it is a blunt one

Until now this project had exactly one way to test an agent: run a scenario.
Materialise a repository, run the agent for minutes, and take the single bit the
hidden tests return ([8](08-evaluation-method.md)).

That bit is the right *acceptance contract* and a poor *instrument*:

- It costs real free-tier quota per sample, so samples are scarce.
- It is noisy at the sample sizes a free tier affords — one configuration scored
  3/3 and 1/3 on consecutive batches of the same scenario
  ([6.4.2](06-agent.md#642-the-pass-column-is-noise)).
- When it says `fail`, it does not say where. The taxonomy exists precisely
  because "it failed" was not actionable
  ([10.3](10-metrics.md#103-failure-taxonomy)).

Meanwhile the failures that recur are mostly *small and specific*: an edit
written against a file the agent never read, a shell operator the backend
refuses, a test deleted because it contradicted the task. Each is a single
decision, and a whole scenario run is a very expensive way to observe one.

## 20.2 What a probe is

A probe puts the **real** agent in front of one situation and stops at its
**first decision**.

Real means real: the system prompt, the tools and the jail come from the
agent's own `build_agent`, and the graph is the compiled one. The probe simply stops reading after the first tool call
([evals/probes.py](../evals/probes.py)). Nothing is reconstructed — a probe that
tested a hand-built copy of the agent would drift from it silently, which is the
failure this design is arranged to avoid rather than to have.

```yaml
- id: reads-before-editing
  agent: code
  why: >
    The recorded failure: "the first edit lands at 08:32:41 against
    alerts/rules.py, which no prior call had read".
  prompt: |
    The alert never fires when the header is written in capitals. Fix it in
    alerts/rules.py.
  files:
    alerts/rules.py: |
      def matches(header, wanted):
          return header == wanted
  expect:
    tool_in: [read_file, ls, glob, grep, task]
    not_tool: edit_file
```

One model call instead of a session.

## 20.3 What it can and cannot say

**A probe cannot tell you an agent solves a problem.** It sees one decision, and
a good first move is not a good run. Nothing here replaces a scenario, and a
promotion decision still rests on the hidden tests
([8.7](08-evaluation-method.md#87-the-promotion-rule)).

**What it can tell you is whether the agent starts the way it should** — and
that is where the recorded failures live. It is also the only instrument in this
project that can be aimed at the improvement agent's own judgement, whose entire
evidence base is one live pass that got the answer wrong
([19.9](19-improvement-agent.md#199-what-the-first-live-pass-showed)). A full
pass costs twenty minutes; four probes cost four calls.

**Every probe cites the failure it guards.** The `why` field is not
documentation, it is the entry condition: a probe with no recorded failure
behind it is a preference, and preferences do not belong in a suite that gates
anything. The test suite asserts that every probe on disk has one.

## 20.4 The expectations

Seven, and mostly negative — because the failures this project records are
things the agent should not have done, and a suite that could only assert the
positive would have to guess the single right answer instead.

| Key | Holds when |
| --- | --- |
| `tool` | the first tool call is this |
| `tool_in` | the first tool call is one of these |
| `not_tool` | this tool is not called in the turn |
| `args_match` / `args_not_match` | a regex over every call's arguments |
| `not_tool_with_args` | no call matches both this tool pattern and this argument pattern |
| `text_matches` | it answered instead of acting, and said this |
| `text_not_matches` | it did not say this, if it answered |
| `no_tool` | it answered instead of acting |

`args_*` searches **every** call in the turn, not just the first: a model that
reads and edits in one response has still edited.

An expectation key nobody implements is refused **at load time**, not at score
time. `expect: {tool_name: read_file}` would otherwise pass forever, and a suite
whose count quietly stops covering what its name says is worse than no suite.
A malformed probe raises rather than being skipped, for the same reason.

## 20.5 LangSmith holds the runs; git holds the claims

A probe run belongs in a LangSmith dataset: a versioned set of examples, an
experiment per run, and a UI where two runs are compared example by example.
That is what a behavioural suite wants and what a terminal table cannot do.

**The dataset is not the source of truth.** A probe's expectation is a claim
about how these agents should behave, argued from a recorded failure, and
`evals/probes/*.yaml` carries the argument beside the assertion where it is
reviewed in a diff. A dataset edited in a browser is a rule nobody reviewed. So
the flow is one-way: files are pushed to the dataset, and the dataset is never
read back over the files. Examples are replaced wholesale rather than diffed,
because an id is stable and an expectation is not — matching on id and updating
in place would leave a dataset silently disagreeing with the files it came from.

All of it is optional. With no key the probes still run and still report; what
is lost is the history and the comparison, not the test
([evals/probe_dataset.py](../evals/probe_dataset.py)).

### 20.5.1 Starting mid-run

Some failures only happen deep in a run. A review signs off a claim it should
have questioned, or a researcher writes a conclusion after five searches that
missed. A first message cannot reach them. For these a probe carries a
`history`: the recorded conversation up to the decision, as `{user: ...}` and
`{ai: ..., calls: [{name, args, result}]}` entries. The agent sees its own past
turns and is judged only on what it says next.

The `explore` and `explore-researcher` targets work this way
([evals/probes/explore.yaml](../evals/probes/explore.yaml)). Their histories are
generated from one run record rather than written by hand, so the model sees
what the failing run saw. The researcher is taken compiled out of the explorer,
not rebuilt next to it.

Two more fields exist because of what the first baseline did:

- **`through`**: tools the agent may call and carry on past, such as a
  reflection or a listing. The judged decision is the first call outside them.
  Without it, three baseline runs "passed" by calling `think_tool` and never
  reaching the write under test.
- **Running out of passes without deciding is a failure.** Doing nothing
  within the allowance satisfies every negative expectation, and scoring it
  as a pass is how the same probe turned green without testing anything.

## 20.6 Running them

```bash
python -m evals probes --list                 # what would run; no calls, no keys
python -m evals probes                        # run them, print the table
python -m evals probes --agent improve        # just one agent's
python -m evals probes --push                 # sync the dataset, run nothing
python -m evals probes --experiment           # run through LangSmith
python -m evals probes --agent explore --dataset free_coding_agent-probes-explore     --experiment --name explore-fix --repetitions 3   # one side of a comparison
```

Serial, always — the probes share one free-tier pool, and running them at once
would make each probe's model mix depend on the others, which is the same reason
eval runs are serial ([evals/run.py](../evals/run.py)).

It exits non-zero on a failure, so it is usable as a gate. Whether it *should*
gate anything is not settled: a probe is one model call against a pool that
changes members between calls, so a single failure is weaker evidence than a
green suite is reassurance. Treat a new failure as a question, not a verdict,
until there is enough history to say how often one flips on its own.

## 20.7 What the first two live runs showed

Seven probes, run twice. **6/7 both times, and a different probe failed each
time — and neither failure was the agent's.**

The first run failed `does-not-edit-the-test-that-contradicts-the-task` because
the agent *read* `tests/test_freeze.py` before acting. That is the correct move
and the one `reads-before-editing` asks for; the expectation was an argument
pattern with no tool attached, and it could not tell reading from writing.
`not_tool_with_args` exists because of it.

The second failed `reads-the-ledger-before-the-traces`, which had **passed on
the first run**. The pool served a different member and the agent wrote a todo
list before it looked at anything, which breaks no rule. The probe had tried to
enumerate every acceptable opening move; it is now stated as the negative it
always meant — *do not open a trace before consulting the ledger* — and renamed
to say so.

Two lessons, both already visible in that small a sample:

- **Prefer the negative.** Enumerating acceptable behaviour means guessing every
  reasonable move a model might make and being wrong about one of them. Naming
  the wasteful or dangerous move does not.
- **A single red probe is a question.** Two runs, two failures, zero agent bugs.
  Every failure so far has been a defect in the probe, which is what a young
  suite should expect and an argument against gating on one until the suite has
  a history.

The agent behaviour these runs actually recorded was good: on the contradiction
probe it opened the documentation, the module and the test before touching
anything.
