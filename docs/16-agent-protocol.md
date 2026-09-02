[← Wiki index](README.md)

# 16. The agent protocol

*How one agent asks another for work. Why the vocabulary is A2A's rather than
ours, why the transport is a Python call rather than an HTTP server, and why the
deliverable is still a file on disk.*

## 16.1 The problem: the human was the message bus

Two agents exist ([6](06-agent.md), [15](15-explorer.md)) and they already share
a workdir, a pool, a jail and a loop. What they do not share is a way for one to
*ask* the other for something. The handoff is a directory:

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
([design note](design/long-run-harness.md)) — and it does not survive a third
agent at all, because the sequencing a human was doing by hand grows
combinatorially and the human is asleep.

### 16.1.1 This reopens a settled decision, and how much of it

[15.1](15-explorer.md#151-what-it-is-for) says, in as many words: *no shared
state, no message bus and no protocol to keep in step* — and gives a good reason,
that the absence of one is what makes it safe to run the two agents hours apart
or to run the explorer once against five coding sessions.

**That property survives, because the protocol does not carry the deliverable.**
A research note is still a Markdown file in `/research`, written by the explorer
and read by whoever opens the directory next, today or next week. What the
protocol adds is the *request* and the *status*: who asked, for what, and whether
it worked. Those are exactly the things a filesystem handoff cannot express, and
exactly the things the human was supplying.

So the reopening is partial and the line is worth stating precisely:

| | Before | Now |
| --- | --- | --- |
| The deliverable | a file in `/research` | **unchanged** |
| Who sequences the two | a human | either — a human still can |
| The request | a human's `question.md` | a `Message`, from a human or an agent |
| Whether it worked | read the directory and guess | a `Task` in a terminal state |

An agent with no peers registered is byte-for-byte the agent that existed before
([`AGENT_PEERS=`](#167-what-this-costs-and-what-is-unmeasured)), which is what
makes the two comparable as configurations.

## 16.2 Why A2A, and why not the alternatives

**[A2A](https://a2a-protocol.org)** (Agent2Agent — Google, donated to the Linux
Foundation) is the standard for exactly this shape: one agent delegates a task to
another and receives a deliverable. Its data model is `AgentCard` for discovery,
`Task` with a lifecycle, `Message`/`Part` for content, and `Artifact` for what
came back. That is a one-to-one fit with what the explorer already does, which is
the main argument for it: nothing had to be bent.

| Alternative | Why not |
| --- | --- |
| **MCP** | It answers a different question. MCP connects an agent to a *tool* or a resource — a stateless call with a typed result. A delegated research job is a long-running, stateful unit of work with a lifecycle and its own artifacts. The industry split is the obvious one: MCP for tools, A2A for agents, and the two compose. |
| A `research` subagent inside the coding agent | This is what `deepagents`' `task` tool already offers, and it dissolves the explorer: the subagent would be a prompt inside the coding session rather than an agent with its own jail, its own tool set and its own record. The explorer exists because a researcher that cannot run the project is a smaller blast radius ([15.5](15-explorer.md#155-what-it-is-allowed-to-do)), and that is a property of a separate agent, not of a prompt. |
| A message format of our own | Cheaper today, and it is a bill that comes due at the third agent and again at the first external one. There is nothing about "ask an agent for a report" that this project understands better than the standard does. |
| FIPA-ACL, and the older academic protocols | Solve a harder problem (speech acts, negotiation, ontologies) that nothing here has. |

**What adopting a standard actually buys, concretely:** the day one of these
agents is worth running on another machine, or a third-party agent is worth
talking to, the work is a transport binding and not a schema migration on records
already written.

## 16.3 Why the transport is local

A2A is defined over JSON-RPC 2.0 and is explicit that transport is a *binding*,
not the protocol. This repo implements a local binding — the "wire" is a Python
call ([agent/protocol/local.py](../agent/protocol/local.py)) — and the objects
that cross it are the ones an HTTP binding would serialize, unchanged.

Three reasons, and the first is specific to this project rather than general
laziness:

1. **Cooldown lives on the provider object, in one process.** Two agents in two
   processes each rediscover which accounts are rate-limited, and each pays a
   wasted request to learn it
   ([13.3](13-roadmap.md#133-known-constraints-that-shape-the-roadmap)).
   In-process, the delegate routes through the *same* provider instances as its
   caller, so quota a delegated search burns is immediately visible to the
   conversation that asked for it. "Two routers over one free tier" is already
   the honest model of this pool
   ([15.4](15-explorer.md#154-which-account-serves-a-search)), and it only holds
   inside one process.
2. **The delegate's calls land in the caller's trace.** One run, one record, one
   `input_tokens` total — the number the whole evaluation rests on
   ([10. Metrics](10-metrics.md)). Split across processes, a delegation spends
   quota that no run's record accounts for, and every efficiency comparison
   silently stops meaning anything.
3. **A server per agent is a port, a supervisor and a dependency**, bought to
   connect two callers that are already in the same interpreter.

None of this argues against HTTP later; it argues for paying for it when a peer
is genuinely remote. `AgentCard.url` is `local:explore` and
`preferredTransport` is `LOCAL` — honest rather than aspirational. An HTTP
binding replaces those two fields and nothing else.

## 16.4 What maps onto what

[agent/protocol/types.py](../agent/protocol/types.py) is the spec's data model,
serializing to the spec's JSON, camelCase included. **The names are not ours and
must not drift**; that fidelity is the entire bet.

| A2A | Here |
| --- | --- |
| `AgentCard` | what an agent says it can do — `explore`'s is in [agent/explore/a2a.py](../agent/explore/a2a.py) |
| `AgentSkill` | one askable job, e.g. `web_research` |
| `Task` | one delegation, with an id, a `contextId` and a lifecycle |
| `TaskState` | `submitted → working → completed \| failed \| rejected` |
| `Message` / `TextPart` | the request, and the delegate's closing word |
| `Artifact` / `FilePart` | one research note, **as a path** |
| `message/send` | `LocalTransport.message_send` — blocking |
| `tasks/get` | `LocalTransport.tasks_get` — re-read a task by id |
| `/.well-known/agent-card.json` | [`AgentRegistry`](../agent/protocol/registry.py), a dict |

Two of those rows are load-bearing and worth their own line.

**An `Artifact` carries a URI, never bytes.** A2A allows inline base64 and this
never writes it. Both agents share a filesystem, so shipping a note through the
protocol would duplicate a file that is already there *and* push several thousand
tokens into the caller's context whether it wanted to read them or not. The
delegate returns a list of paths; the caller reads the ones it needs with the
tool it already has, and pays for exactly that.

**Discovery is prose in the system prompt, not a `list_agents` tool.** Tool
schemas are 91% of what a step spends
([6.4](06-agent.md#64-why-it-is-shaped-this-way)), and a directory that changes
once at startup would otherwise be charged on every step of every run. It is
rendered once into the prompt by
[`directory_section`](../agent/protocol/registry.py), where it is already in
front of the model at the moment it decides whether to delegate at all. So the
protocol adds exactly **one** tool.

## 16.5 One delegation, end to end

```
coding agent          delegate(agent="explore", request="…")
  └─ LocalTransport   Task(id, contextId) → submitted → working
       └─ registry    explore's handler, closed over the same router
            └─ explore session   tavily_search / think_tool, writes /research/*.md
       ← Task          completed, artifacts=[research/cerebras-limits.md]
  ← tool result        the state, the closing line, the paths
```

What the caller reads back is deliberately thin:

```
task 5119aa42-… to `explore` — **completed**

Wrote one note comparing the three providers, with 6 sources.

Artifacts (read these files for the detail):
- `research/cerebras-limits.md` (4,211 bytes)
```

Four behaviours here exist because their absence is a specific failure:

- **Only the notes *this* task wrote are reported.** The handler snapshots the
  research directory before and after. Without that, a second delegation returns
  the first one's files as its own findings and the caller reads a stale note
  believing it answers the new question.
- **A delegate that crashes returns a `failed` Task, not an exception.** The
  subordinate failing is not the caller failing; ending the parent run over it
  would throw away work already done.
- **An unknown agent is `rejected`, with the reachable names.** The caller is a
  language model reading a tool result, and a refusal it can correct beats a
  traceback that ends the run.
- **`completed` with no artifacts says so loudly.** The delegate ran, spent
  quota and wrote nothing down; silence there reads as success and is not.

## 16.6 What a delegation costs

**A whole agent session.** Not a call — a session: 14–21 model calls in the
measured range, against a flash tier of twenty requests per day per model per
account ([5.4](05-providers.md#54-current-free-tier-limits)). One delegation can
plausibly cost more than the coding turn that asked for it.

Nothing enforces a ceiling today. That is a decision, not an oversight, and the
reasoning is that a limit invented before a single run has been observed is a
number pulled from the air — the honest first move is to let it run and read
`delegated_tasks` and `input_tokens` off the record. What *is* in place is the
slot for the answer: A2A has no concept of cost, so a per-task budget belongs in
`Task.metadata`, which the spec reserves for exactly this, and the field is
threaded through `message_send` from the start. Adding a budget later is a
policy change in one function, not a schema change.

What is spent is instead made visible. The tool description tells the model
plainly that a call is slow and spends a shared budget; the delegate's calls are
written to the same `EVAL_TRACE_FILE` as the caller's, so a run's totals include
them; and the run record carries `peers` and `delegated_tasks` so nobody reads a
token count without knowing a delegation happened.

Every terminal `Task` is also written to `a2a/<task-id>.json` beside the run
record — what was asked, what came back, which files. Beside it, never inside the
workdir: the coding agent commits its workdir, and a protocol log committed into
the project under review is noise in every diff it produces afterwards.

## 16.7 What this costs, and what is unmeasured

**This branch is a configuration and it has not been measured.** By this repo's
own rule a harness change is decided by evaluation, not argument
([13.7](13-roadmap.md#137-how-to-propose-a-change)), and what is written above is
argument. Specifically unknown:

- whether a coding agent that *can* delegate delegates when it should, or reaches
  for the web on questions it could answer by reading the repo;
- what a delegating run costs against the non-delegating baseline;
- whether the closing line plus a list of paths is enough for the caller to use
  the note, or whether it reads the file and then asks again anyway.

`AGENT_PEERS=` (empty) turns delegation off and restores the previous agent
exactly, so the A/B is one environment variable.

## 16.8 What is deliberately not built

- **Streaming and push notifications.** `message/stream` and SSE exist in A2A;
  a blocking local call has no gap to stream across. The caller waits.
- **`input-required` round trips.** Both agents run headless with nobody to ask.
  The state is modelled because a delegate that needs a decision should
  eventually be able to say so rather than guess, and unreachable is not the same
  as unrepresentable.
- **Authentication.** There is no network to authenticate over. It arrives with
  the HTTP binding or not at all.
- **Delegation in the other direction.** The explorer gets no transport, so it
  cannot call the coding agent — and therefore cannot recurse into it. Not a
  policy so much as an absence: nothing has needed it, and a cycle between two
  agents that each spend a session per call is an expensive thing to discover by
  accident.

## 16.9 Adding a third agent

The point of the shape. `agent/code` and `agent/explore` do not import each
other; [agent/protocol/peers.py](../agent/protocol/peers.py) is the only module
that knows about both. A third agent is:

1. an `AgentCard` and a handler `(Task) -> Task`, next to that agent's session —
   `explore`'s pair is ~90 lines in [a2a.py](../agent/explore/a2a.py) and
   changes nothing about how the explorer runs;
2. one branch in `peers.build_transport`, gated on a capability *probe* rather
   than a declaration — the explorer is registered only if the pool can actually
   search, because an agent advertised and then unreachable costs the caller a
   delegation to discover
   ([15.2.1](15-explorer.md#1521-a-capability-is-a-fact-to-probe-not-to-infer)).

The calling agent changes not at all: it has one `delegate` tool and a directory
of cards, and knows nothing about what is behind them.

## 16.10 What the first live run showed

*One delegation, on the real pool, 2026-08-27. The coding agent was briefed to
build an eval scenario adapted from SWE-bench and told to research it first.*

**The protocol worked.** One `delegate` call; the task went
`submitted → working → completed`; the explorer ran 13 searches and wrote a
27 KB cited note; the artifact came back as a path; the coding agent read it and
started building. Failover ran underneath it — a 429 on the first Gemini
account rerouted to the second mid-delegation — and the shared cooldown behaved,
because it is one process
([16.3](#163-why-the-transport-is-local)).

The run then died on a bug unrelated to any of that (below), so what it settles
is that the mechanism works, not what it costs.

### 16.10.1 Three bugs, one of them ours

| Where | What | Fix |
| --- | --- | --- |
| `agent/explore/a2a.py` | **Every delegated event was traced twice.** The handler added `tracer_from_env()` to the child config; the child already inherits the caller's handlers through LangChain's run context. The caller's own tools appeared once each and the delegate's `web_search` twenty times for ten searches. | The child gets no callbacks of its own. |
| `deepagents` filesystem jail | `Path.resolve()` on Windows returns the `\?\` extended-length form when another process holds the file open, and the root was resolved once without it — so a write **inside** the jail is refused as an escape. Three files into a directory, the fourth was refused, and it killed the session. | `RestrictedShellBackend._resolve_path` normalizes both sides ([agent/runtime/backend.py](../agent/runtime/backend.py)). |
| `agent/runtime/backend.py` | The git allowlist denied `--delete` and `-D` and **not `-d`** — and the agent used `-d`. A list that claims to forbid deletion while permitting the spelling an agent reaches for first is worse than no list. | `-d` denied. |

The first is the one worth dwelling on: it inflated `input_tokens`, the single
number the whole comparison rests on
([10. Metrics](10-metrics.md)), and it did it *only* for delegated work — so the
configuration that delegates would have looked more expensive than it is, and
the inflation would have been read as a property of delegation.

### 16.10.2 A measurement gap, and a wrong conclusion it nearly produced

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

### 16.10.3 Where both agents drifted without failing

Neither agent failed at anything it was asked to do, and both left their
instructions. The explorer's four divergences and what changed because of them
are in [15.7](15-explorer.md#157-measured-against-a-reference-research-agent);
the coding agent created a throwaway branch, deleted it, and committed an edit
to a `README.md` that was not part of its brief and is shared across every
scenario branch.

**This is what the new checks are for.** A pass/fail metric moves on none of it
([evals/research_trajectory.py](../evals/research_trajectory.py)), which is why
a research run needed its own instrument rather than the coding run's.

---

**Previous:** [← 15. The web explorer](15-explorer.md) · **Next:** [17. Deployment →](17-deployment.md)
