[← Wiki index](../README.md)

# Delegation

*How one agent asks another for work. Why it is a command rather than a
protocol, what that costs, and why the deliverable is still a file on disk.*

## The problem: the human was the message bus

Three agents exist ([The coding agent](code.md), [The web explorer](explore.md),
[The improvement agent](improve.md)) and they already share a workdir, a pool, a
backend and a loop. What they did not share was a way for one to *ask* another for
something. The handoff was a directory:

```bash
python -m agent.explore ./project < question.md   # writes /research
python -m agent.code    ./project < brief.md      # reads it
```

Which means a person decides that research is needed, writes the question, runs
the explorer, reads what came back and then writes the brief. The coding agent
cannot notice mid-task that it does not know something and go find out. Every
piece of outside knowledge in a run was put there by a human before the run
started.

That is fine for two agents and a person watching. It is the wrong shape for
Phase 2 — an agent working unattended for hours
([design note](../design/long-run-harness.md)) — and it does not survive a third
agent at all, because the sequencing a human was doing by hand grows
combinatorially and the human is asleep.

### This reopens a settled decision, and how much of it

[What it is for](explore.md#what-it-is-for) says, in as many words: *no shared
state, no message bus and no protocol to keep in step* — and gives a good reason,
that the absence of one is what makes it safe to run the two agents hours apart
or to run the explorer once against five coding sessions.

**That property survives, because nothing carries the deliverable.** A research
note is still a Markdown file in `/research`, written by the explorer and read
by whoever opens the directory next, today or next week. What delegation adds is
the *request* and the *status*: who asked, for what, and whether it worked.

| | Before | Now |
| --- | --- | --- |
| The deliverable | a file in `/research` | **unchanged** |
| Who sequences the two | a human | either — a human still can |
| The request | a human's `question.md` | `--task`, from a human or an agent |
| Whether it worked | read the directory and guess | the command's exit code and its final message |

An agent with no peers is byte-for-byte the agent that existed before
([`AGENT_PEERS=`](#what-this-costs-and-what-is-unmeasured)), which is what
makes the two comparable as configurations.

## Why a command, and not a protocol

There was a protocol here, and it is gone. It was
[A2A](https://a2a-protocol.org)'s data model — `AgentCard`, `Task`, `Message`,
`Artifact` — on a local transport, with a registry, a task store, a `delegate`
tool and a per-agent handler beside every session. About 700 lines to connect
callers that were already in the same interpreter.

**The observation that deleted it: every agent here is already a command.**

```bash
python -m agent.code    ./project --task "..."
python -m agent.explore ./project --task "..."
python -m agent.improve .         --task "..."
```

Same shape, same three arguments, one summary on stdout. And the coding agent
already has `execute`, because it has to run its own tests
([The blast radius](code.md#the-blast-radius)). So delegation needs no transport, no
registry and no tool — it needs the agent to **know those commands exist**,
which is a paragraph of prose, and it needs one caller in `agent/improve` to be
able to launch one, which is `subprocess.run`. That is
[agent/delegation.py](../../agent/delegation.py), and it is a fifth the size of
what it replaced.

The vocabulary argument for A2A was real and it still holds — for
[serving](../operations/serving.md), where a caller is genuinely remote, a run outlives a
request, and something has to model a task with an id to poll. That is the only
place the vocabulary survives, and it is the place it was designed for.

### What the prompt gains, and what it does not

A `delegate` tool was a schema in front of the model on **every step of every
run**, describing a directory that changes once at start-up. Tool schemas are
91% of what a step spends ([Why it is shaped this way](code.md#why-it-is-shaped-this-way)), so
that is not a tidiness argument. The replacement is a prompt section, charged
once, that names the command:

```
**explore** — Researches the open web and writes a cited report…
- delivers: markdown notes under `/research` — read them with your file tools
- run it: `python -m agent.explore . --task "<your brief>"`
```

The delegating configuration therefore differs from the baseline by a paragraph
and **no tool at all** — asserted in
[tests/agent/test_delegation.py](../../tests/agent/test_delegation.py), because
that is the property that makes `AGENT_PEERS=` a valid A/B arm.

### The roster and the how-to are a skill

The prompt section above did two jobs at two different rates. Which agents can
be reached, and where each one runs, is decided when the run starts and differs
between runs. How to delegate well — whether it is worth an hour of the pool at
all, how to write a brief for someone who cannot see your conversation, why the
closing summary is not the deliverable — is the same prose every time, and
**most runs never delegate**.

Both moved into `delegate`, a skill ([Skills](code.md#skills)). The roster
goes with them: it is rendered into the skill per run rather than committed, so
it still names only what was probed and found reachable. What is left in
`system.md` is one sentence:

```
### Delegating

Some work belongs to another agent rather than to you. Before you decide it
does, and before you run any `python -m agent.*` command, read the `delegate`
skill.
```

Everything else — each command, what it leaves on disk, the good and bad brief —
is read when the agent decides it applies, and not before.

This does not change the property the A/B depends on. A skill is a prompt
section and `read_file`; it is still **no tool**. And `AGENT_PEERS=` now removes
more than it did: no roster, no skill, and no skills middleware at all, so the
baseline prompt is byte-for-byte what it was
([Rendered per run, not committed](code.md#rendered-per-run-not-committed)).

**What this cost.** The prose that left the prompt was ~970 characters; the
mechanism costs ~2,000, so the delegating arm's system prompt grew by about a
thousand characters rather than shrinking. The saving arrives with the second
and third skill ([One skill does not pay for itself yet](code.md#one-skill-does-not-pay-for-itself-yet)).

**A bug this surfaced.** `AGENT_PEERS` was documented on `python -m agent.code`
and read by nothing: the CLI called `run()` without `peers`, so every coding run
started from the command line was alone regardless of the setting. Only
`agent/improve` and `agent/serve` ever probed. The CLI now calls
`delegation.available()` like they do — which means the numbers above are also
the first ones measured on a CLI run that actually had peers.

## What a delegated child needs

The child is a real process, and three things it needs are not what a shell
command ordinarily gets. Two come from the backend's environment and the third
the model asks for per call.

| What | Where it comes from | Why |
| --- | --- | --- |
| The pool's API keys | `inherit_env=True` on the backend | a delegated session **is** the pool's consumer; without keys it dies at start-up with "no providers loaded" |
| This repository on `PYTHONPATH` | `env=` on the backend ([`local_shell`](../../agent/code/agent.py)) | the workspace is routinely some *other* project, where `import agent` does not resolve |
| An hour instead of 300s | the model passes `timeout=3600` on the `execute` call | an agent session is tens of minutes to hours; the ordinary ceiling would kill every delegation after the quota was already spent |

The last one is prose, not Python. Nothing in the harness knows that one command
is a delegation — an earlier version sniffed `argv` for `python -m agent.*` and
granted the longer timeout itself — so the
[`delegate` skill](../../agent/code/skills/delegate/SKILL.md) tells the model to
ask for it, and 3600s is the most deepagents' `execute` accepts. A delegation
killed at 300s has already spent its brief and some of its quota, so this is
worth checking in a trace when one comes back empty.

**`--task` exists because the child gets no stdin**: `execute` runs its command
with stdin at `/dev/null`, so the brief has to be sayable on the command line.

## Why a subprocess costs something real

The old transport was in-process for two good reasons, and one of them is now
genuinely lost.

1. **Cooldown lives on the provider object, in one process**
   ([Known constraints that shape the roadmap](../status.md#known-constraints-that-shape-the-roadmap)). A
   delegate in a second interpreter builds its own router, so an account the
   caller has already benched is one the delegate rediscovers — at the price of
   one refused request. **This is a real regression and it is not bought back.**
   What bounds it is that a delegation *blocks*: the caller is idle while the
   child runs, the two never route concurrently, and the usage ledger they both
   append to stays a single writer at a time.
2. **The delegate's calls landed in the caller's trace.** This one survives. The
   child inherits `EVAL_TRACE_FILE`, which is the flat JSONL every metric is
   summed over ([Metrics](../evaluation/metrics.md)) and is appended to rather than
   replaced, so a delegation's cost is still inside the totals of the run that
   asked for it. `AGENT_TRACE_FILE` is *not* inherited: the run tree is one JSON
   file per run, and a child writing the parent's path would overwrite the
   record of the run that launched it.

## What a delegation costs

**A whole agent session.** Not a call — a session: 14–21 model calls in the
measured range, against a flash tier of twenty requests per day per model per
account ([Current free-tier limits](../pool/providers.md#current-free-tier-limits)). One delegation can
plausibly cost more than the coding turn that asked for it.

`AGENT_DELEGATE_TIMEOUT` bounds the wall clock (four hours by default) and
nothing bounds the tokens. That is a decision, not an oversight: a limit
invented before a single run has been observed is a number pulled from the air,
and the honest first move is to read `tokens_in` off the record.

What is spent is instead made visible. The prompt section tells the model
plainly that a call is slow and spends a shared budget; the child's calls land
in the caller's `EVAL_TRACE_FILE`; and the run record carries `peers`, so nobody
reads a token count without knowing delegation was available.

### The verdict comes from git, not from the delegate

A coding session's deliverable is a commit, so what a caller reads back leads
with what the repository says and only then quotes what the session said about
itself ([gitstate.py](../../agent/utils/gitstate.py)):

```
**Nothing changed.** On `master`, no commit was made and the working tree is
clean — the head is the same one the task started from. Whatever the closing
message says it did, the repository disagrees.
```

This is not hypothetical caution. The first delegation this project ever made
came back describing three changes to `session.py` that the diff did not contain
([What it costs, and what is unmeasured](improve.md#what-it-costs-and-what-is-unmeasured)), and the
evidence that would have settled it was already computed and being dropped on
the floor. The same rule holds for the explorer, whose CLI ends by listing the
notes it wrote — an empty list is the loudest thing it can print.

## What this costs, and what is unmeasured

**This is a configuration and it has not been measured.** By this repo's own
rule a harness change is decided by evaluation, not argument
([How to propose a change](../evaluation/method.md#how-to-propose-a-change)), and what is written above is
argument. Specifically unknown:

- whether a coding agent that *can* delegate delegates when it should, or
  reaches for the web on questions it could answer by reading the repo;
- what a delegating run costs against the non-delegating baseline;
- whether a small model reliably quotes a multi-line brief correctly into
  `--task`, which is a failure mode the old tool's JSON schema did not have;
- how often the lost cooldown sharing costs a refused request in practice.

`AGENT_PEERS=` (empty) turns delegation off and restores the previous agent
exactly, so the A/B is one environment variable.

## What is deliberately not built

- **A budget per delegation.** See above: no number until there is a
  distribution to read one off.
- **Non-blocking delegation.** The caller waits. A background child would need
  supervision, a way to report back into a conversation that has moved on, and
  two routers on the pool at once — which is the thing the blocking call
  prevents.
- **Delegation in the other direction.** The explorer gets no peers, so it
  cannot call the coding agent and therefore cannot recurse into it. Not a
  policy so much as an absence: nothing has needed it.
- **A cycle check.** Not needed, because there is a simpler invariant: a child
  is launched with its own name removed from `AGENT_PEERS`, so a delegation
  cannot come back round to the agent that made it. Filtering rather than
  replacing is what keeps `AGENT_PEERS=` meaning what it says — an operator who
  turned delegation off must not get it back through a session someone else
  delegated.

## Adding a fourth agent

The point of the shape. `agent/code`, `agent/explore` and `agent/improve` do not
import each other; [agent/delegation.py](../../agent/delegation.py) is the only
module that knows about all of them. A fourth agent is:

1. a `__main__.py` in the same shape as the other three — workdir, `--task`,
   final message on stdout;
2. one entry in `delegation.AGENTS`: its module, what it is, and what it leaves
   behind.

There is no card, no handler, no registration and no tool. The calling agent
changes not at all: it gains one more line in a prompt section and knows nothing
about what is behind it.

Availability is still a *probe* rather than a declaration — `explore` is offered
only if the pool can actually search, and `scenarios` only if that repository is
really there, because an agent advertised and then unreachable costs the caller
a whole session to discover
([A capability is a fact to probe, not to infer](explore.md#a-capability-is-a-fact-to-probe-not-to-infer)).

## What the first live delegation showed

*One delegation, on the real pool, 2026-08-27, over the transport that has since
been deleted. The coding agent was briefed to build an eval scenario adapted
from SWE-bench and told to research it first.*

**The mechanism worked.** One delegation; the explorer ran 13 searches and wrote
a 27 KB cited note; the path came back; the coding agent read it and started
building. Failover ran underneath it — a 429 on the first Gemini account
rerouted to the second mid-delegation.

The run then died on a bug unrelated to any of that, so what it settles is that
the mechanism works, not what it costs. Three of the four findings below outlive
the transport that produced them.

### Three bugs, one of them ours

| Where | What | Fix |
| --- | --- | --- |
| the delegation handler | **Every delegated event was traced twice.** The handler added a tracer to the child config; the child already inherited the caller's handlers. The caller's own tools appeared once each and the delegate's `web_search` twenty times for ten searches. | The child got no callbacks of its own. Moot now — a subprocess cannot inherit them, and it appends to the same file instead. |
| `deepagents` `FilesystemBackend` | `Path.resolve()` on Windows returns the `\?\` extended-length form when another process holds the file open, and the root was resolved once without it — so a write **inside** the workspace is refused as an escape. Three files into a directory, the fourth was refused, and it killed the session. | Was fixed by a subclass; **live again** since the harness moved to the stock backend ([Why the restrictions went](code.md#why-the-restrictions-went)). |
| the old `RestrictedShellBackend` | The git allowlist denied `--delete` and `-D` and **not `-d`** — and the agent used `-d`. A list that claims to forbid deletion while permitting the spelling an agent reaches for first is worse than no list. | Moot: the allowlist is gone and `git` is unfiltered. |

The first is the one worth dwelling on: it inflated `tokens_in`, the single
number the whole comparison rests on ([Metrics](../evaluation/metrics.md)), and it did
it *only* for delegated work — so the configuration that delegates would have
looked more expensive than it is, and the inflation would have been read as a
property of delegation.

### A measurement gap, and a wrong conclusion it nearly produced

The tracer clipped every field to its first 2,000 characters. A `web_search`
result carries its `Sources:` block **last**, so every recorded search looked
source-less — which reads as *the model answered from memory and the report
laundered it into a citation*, the exact failure the explorer exists to prevent.

It was an artifact. The citations were checked by hand and are real. But the
trace could not answer the question, and a post-mortem that stopped one step
earlier would have filed a serious finding that was not true. `_clip` now keeps
a tail as well as a head, for the same reason: a `write_file` carries the path
*after* the file content, so the record could not say which file a run wrote
either.

### Where both agents drifted without failing

Neither agent failed at anything it was asked to do, and both left their
instructions. The explorer's four divergences and what changed because of them
are in [Measured against a reference research agent](explore.md#measured-against-a-reference-research-agent);
the coding agent created a throwaway branch, deleted it, and committed an edit
to a `README.md` that was not part of its brief and is shared across every
scenario branch.

**This is what the new checks are for.** A pass/fail metric moves on none of it
([evals/research_trajectory.py](../../evals/research_trajectory.py)), which is why
a research run needed its own instrument rather than the coding run's.
