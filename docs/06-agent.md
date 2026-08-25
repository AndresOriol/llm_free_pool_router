[← Wiki index](README.md)

# 6. The coding agent

*Two architectures over one pool and one jail, and the comparison that is meant
to end with one of them deleted. What each can do, what neither is allowed to
do, and where every branch is decided.*

## 6.1 Two architectures, one question

Both run the same shape of job — a **session**: one long unattended run over one
project, task on stdin, process exits at EOF, every model call routed through
the pool ([4. Failover](04-failover.md)). They disagree about one thing: what a
single call is allowed to contain.

| | `agent/harness/` — narrow roles | `agent/deep/` — one conversation |
| --- | --- | --- |
| Run it | `python -m agent.harness workdir < brief.md` | `python -m agent.deep workdir < brief.md` |
| A call holds | one role's slice of a shared log ([6.5](#65-what-each-role-sees)) | the conversation, compacted when it grows |
| Fits | the pool's **narrowest** member (8,000 tokens) | the pool's **widest** (≥128,000, enforced) |
| Built from | this repo ([6.4](#64-the-graph)) | `create_deep_agent`, configured like `deepagents-code` ([6.9](#69-the-deepagents-arm)) |
| Its record | four files under `.harness/` ([7.6](07-observability.md#76-what-a-session-records-about-itself)) | one LangSmith run tree ([7.7](07-observability.md#77-the-record-one-run-tree)) |
| Branch | `harness/adhoc-router` | `harness/deepagents` |

**Why the second one exists.** The narrow-role design is an answer to a
constraint: a call must fit an 8,000-token Groq member, so work gets split until
it does. That constraint has been **lifted for coding work**. A Groq account
holds 100,000 tokens *per day*, so a single wide request would spend the whole
day's budget — those members can never serve a conversation, and pretending they
might is what forced the splitting. A coding session now declares a hard floor
and routes only above it ([4.2](04-failover.md#42-size-aware-selection)); Groq
stays in the pool for work that fits it.

**Why this is not simply a reversal.** A conversational loop was built first, on
[deepagents](https://github.com/langchain-ai/deepagents), and lost: 226,854
input tokens against 5,756 on the same task, replicated on every rep of every
batch ([6.8.1](#681-the-cost-result-is-the-one-that-replicated)). That result
still stands, and it is the reason this is a *configuration* rather than a
merge. What has changed since is not the argument but its inputs — the SDK now
ships summarization, message eviction and tool-output offloading, and the pool's
floor for this work is 128,000 tokens rather than 6,000. Whether that is enough
to move a 39× gap is an empirical question, and the first measurement is not
encouraging ([11.3](11-eval-status.md#113-where-the-numbers-stand)).

**A draw keeps the simpler configuration**
([8.7](08-evaluation-method.md#87-the-promotion-rule)) — and "simpler" here means
the one this repo does not have to maintain.

`workdir` is the project in both. The task arrives on stdin and the process
exits at EOF, which is what makes either drivable from a script or from an
orchestrating agent ([12.5](12-development-harness.md#125-driving-the-free-agents)).

## 6.2 The blast radius

Two independent restrictions, both worth understanding before pointing this at
anything you care about.

**Filesystem jail.** The backend is rooted at `workdir`. The agent's `/` *is*
the workdir; absolute paths and `..` cannot escape it. A brief should tell the
agent to write at `/`, not at a host path.

**Execution allowlist.** Only `python` and `pytest` (plus `git`, below), named
bare with no path, `shell=False`, secrets stripped from the child environment,
and a 300s timeout ([backend.py](../agent/runtime/backend.py)).
Shell syntax is *refused* rather than passed through as a literal argument —
accepting it silently once cost a run 900 seconds in heredocs that hung until
the timeout.

**Git, by subcommand.** A session commits its own work incrementally on its own
branch, so the human's gate is the **merge**, not the commit. `push`, `merge`,
`rebase`, `reset` and `clean` are refused
([design note §4.1](design/long-run-harness.md#41-git)).

This is a **small blast radius, not a sandbox**. `python` is arbitrary code
execution. For real isolation, run the whole thing inside a container — which
becomes a prerequisite the day sessions run unattended overnight.

## 6.3 The agent's instructions

The session reads the project's `NOTES.md` and appends it to the task, so the
unit of work is a project's feedback file rather than a task string. Notes in,
notes out: the session appends its own account to the same file
([design note §1](design/long-run-harness.md#1-what-is-actually-being-built)).

Whether a codebase ships curated context at all is itself a variable worth
measuring, which is why scenarios record it as `context_mode`
([9.4](09-scenarios.md#94-scenarioyaml)).

## 6.4 The graph

[agent/harness/graph.py](../agent/harness/graph.py). One node per role, plus the
orchestrator. This diagram is printed by `graph.mermaid()` from the compiled
graph and pinned by a test, so it cannot drift from the code:

```mermaid
graph TD;
	__start__([__start__]):::first
	orchestrate(orchestrate)
	explore(explore)
	write(write)
	execute(execute)
	document(document)
	review(review)
	__end__([__end__]):::last
	__start__ --> orchestrate;
	document --> orchestrate;
	execute --> orchestrate;
	explore --> orchestrate;
	orchestrate -.-> __end__;
	orchestrate -.-> document;
	orchestrate -.-> execute;
	orchestrate -.-> explore;
	orchestrate -.-> review;
	orchestrate -.-> write;
	review --> orchestrate;
	write -.-> execute;
	write -.-> orchestrate;
```

Every worker returns to the hub, so the orchestrator decides every step. The one
exception is the direct `write → execute` edge.

**The orchestrator proposes; the code vetoes.** Each deterministic branch is
both a model call not spent on a decision with one right answer, and a way the
run cannot go wrong. All of them are in `_veto` or an edge function:

| Refusal | Why |
| --- | --- |
| `DONE` with nothing executed becomes `EXECUTE` | An unverified "done" is the `stopping` failure wearing a confident face |
| `DONE` with nothing reviewed becomes `REVIEW` | Nobody else is going to look |
| A third consecutive `EXPLORE` becomes `WRITE` | Observed: nine of twelve steps were `explore`, re-reading the same four files. Reading is the move an orchestrator can always justify |
| An unparseable action becomes `EXPLORE` | The read-only worker is the safe default, never one that writes |
| An applied edit goes straight to `EXECUTE` | "Check what you just changed" has one right answer |

**What is graph state and what is not.** The state holds only what an edge
reads: the next worker, its brief, whether a review passed, the step count. The
log, the journal, the transcript and git are collaborators the nodes
close over. That is also why a session that exhausts its budget still writes its
rationale — everything it learned is already on disk, not in a state object that
died with the graph.

**The budget counts journal steps**, checked in the orchestrate node.
LangGraph's `recursion_limit` counts supersteps and is only a backstop; the
number that matters is the one the scenario timeout and the cost figures are
reasoned about in.

## 6.5 What each role sees

Everything the session knows is one ordered log of entries, each tagged with a
*kind* and the node that wrote it ([log.py](../agent/harness/log.py)). A node
declares the kinds it reads ([nodes/](../agent/harness/nodes/)) and is handed
those entries as messages — the last few of each kind, and nothing else.

This is the mechanism, and the one table worth memorising:

| role | task | files | notes | edits | exec | diff | steps | tier |
| --- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | --- |
| **orchestrate** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | wide |
| explore | ✓ | | | | | | | any |
| **write** | ✓ | ✓ | | | | | | any |
| execute | ✓ | | | ✓ | | | | any |
| document | ✓ | | | ✓ | | ✓ | | wide |
| review | ✓ | | ✓ | | ✓ | ✓ | | wide |

Read the `write` row. It sees the task and a list of candidate files, and
nothing else — not the diff, not the notes, not what the last command returned.
**Everything else has to arrive copied into the `CONTEXT` field of its brief.**

That is the design, not an oversight: **context is pushed down by the one role
routed to a wide-context member, never pulled up by roles that cannot afford
it** ([design note §8.1](design/long-run-harness.md#81-the-handoff-envelope)). A
small model told to "go read the notes for background" mostly will not, and will
act on what it has; pushing removes the failure instead of instructing against
it. The cost is that the orchestrator becomes a single point of failure for
every role's quality, which is why a worker can answer
`INSUFFICIENT_CONTEXT` rather than guess.

**`tier`** is a claim about the job, not the request. A role whose work is
judgement over a wide view declares a floor and the router honours it
([4.2](04-failover.md#42-size-aware-selection)). Splitting work to fit the
narrowest pool member is what makes this cheap; doing it to a role that needs
breadth is what makes it stupid.

## 6.6 One role call

[`run_node`](../agent/harness/nodes/base.py) is the inside of every node, and it
is three lines of idea:

```python
messages = [SystemMessage(node.prompt), *log.view(node.reads), HumanMessage(ask)]
# ... up to node.max_rounds of tool calls ...
return NodeResult(text=..., outputs=..., calls=..., prompt=...)
```

`ask` is the orchestrator's brief. Then **the message list is discarded.** Only
the `NodeResult` escapes, and only what the graph appends to the log survives
into the next step. No role ever sees another role's conversation.

That is what bounds a prompt by the role's declaration rather than by how long
the run has been going — step 40 costs what step 1 cost — and it is why this is
hand-written rather than a prebuilt LangGraph agent. `create_react_agent`
accumulates the conversation, which is precisely the cost this exists to avoid;
and `max_rounds` and `force_summary` both come from observed failures and have
no prebuilt equivalent.

**`force_summary`** spends the final round with no tools bound. Without it a
role that used every round on tool calls ends holding a tool-calling response,
whose text is empty — it does all the work and reports nothing. It is applied
only to roles whose value is what they *say*; forcing it on an acting role
steals the round it needed to act.

**Acting roles are judged by their tool effects, never by their summary.** An
`execute` that says "tests pass" about a failing run must not be able to end a
session, and under a review model where nobody reads the code it would end it
convincingly. Observed, verbatim, from an `explore` role holding no edit tool
and no shell: *"Implemented support for spelled-out units… All tests pass (4
passed)."*

## 6.7 What failover looks like in practice

Expect a single step to walk several small-TPM Groq members before a
higher-capacity account accepts the request. That is the design working — but it
is also why the pool drains fast, and why `failover_bounces` is a first-class
metric ([10.2](10-metrics.md#102-automatic-metrics)).

Free tiers signal limits inconsistently — Groq uses HTTP 429 *and* 413 for
tokens-per-minute — and both are transient
([4.3](04-failover.md#43-classifying-a-failure)). A model the platform has
retired is not: it comes back as a 404, and the router drops that member from
the pool for the rest of the process rather than dying on it.

**Cheaper agents trip landmines expensive ones never reach.** A dead model is
only routed to if a request is small enough to pass the size filter, so
shrinking requests reaches *further* into the pool. Audit the pool before
reading any efficiency result.

## 6.8 Why it is shaped this way

Three findings from the runs that produced this architecture. The configurations
themselves are in `git log`; these are what they showed, and they still hold.

### 6.8.1 The cost result is the one that replicated

Same task, same pool, interleaved: **226,854 input tokens** through a
conversational loop against **5,756** through narrow roles, stable across every
rep and every batch, with between-configuration spread far exceeding
within-configuration variance. Provider calls and failover bounces moved the
same way. This is the result the architecture rests on.

### 6.8.2 The pass column is noise

One configuration scored **3/3 in one batch and 1/3 in the next**, unchanged,
hours apart. The noise floor is around 15 points at ten times these sample sizes
([8.6](08-evaluation-method.md#86-fair-comparison)). Cost metrics replicate;
pass rates do not. Any ranking read off a pass column at n<10 is invented, and
this project has had to retract one.

### 6.8.3 Every failure is `reasoning`

**12 of 13 recorded failures**, with zero `retrieval` and zero `tooling`. Every
configuration found the file, edited it, and ran the tests — and was
conceptually wrong. No change of topology moves that, which is why more
architecture work was not the next move and scenario authoring was
([13.2](13-roadmap.md#132-what-to-do-next)).

A taxonomy where one class holds 92% of the mass is a rename of "failed", not a
diagnosis. Subdividing it is the standing job of the batch analysis
([design note §6.3](design/long-run-harness.md#63-why-the-existing-taxonomy-needs-subdividing-urgently)).

### 6.8.4 Closing the loop is not the same as being right

The textbook instance, from a run whose own tests passed:

```diff
-    value = headers.get("retry-after")
+    value = headers.get("Retry-After")
```

The scenario is about **case-insensitive** lookup. The agent flipped the case to
satisfy the test it could see; the hidden set, which checks other casings,
failed it. This is what the withheld-test design exists to catch
([9.3](09-scenarios.md#93-anatomy)), and it is not a token-budget problem. **A
cheaper agent reaches this failure mode sooner, not later.**

## 6.9 The deepagents arm

[agent/deep/](../agent/deep/). Almost none of this is agent design.
`create_deep_agent` already assembles the todo list, the filesystem tools, the
subagent `task` tool and summarization, and the `execute` tool switches itself
on because `RestrictedShellBackend` satisfies `SandboxBackendProtocol`. Two
things are worth knowing.

### 6.9.1 The pool drops in with no adapter

`resolve_model` returns a `BaseChatModel` unchanged, and `RouterChatModel` is
one. So the pool is passed where a model id would go, and every failover
guarantee on [4. Failover](04-failover.md) holds inside a harness this repo did
not write. That is the whole integration.

The floor is enforced, not preferred. `for_context(128_000, strict=True)` makes
the router *refuse* to route below it and wait for a wide member to leave
cooldown, rather than falling back to a narrow one
([4.2](04-failover.md#42-size-aware-selection)). Selection and the cooldown wait
have to be asked the same question — a wait computed over the whole pool reports
"someone is free" because a Groq member is warm, and the run dies with a wide
member seconds from returning.

### 6.9.2 What makes a deep agent a *coding* agent

The configuration is ported from `deepagents-code`'s `create_cli_agent` (MIT):
the generated system prompt, the project overview put in front of the model, and
a shell allowlist that refuses a command **as a tool message** rather than as an
exception, so the model reads the reason and corrects itself instead of retrying.

| Ported | Where |
| --- | --- |
| System prompt: understand → build → test → verify; match the spec exactly; parallel tool calls; paginated reads; git safety; root-cause debugging; stop after three identical failures | [system_prompt.md](../agent/deep/system_prompt.md) |
| Prompt assembly and its interpolated sections | [prompt.py](../agent/deep/prompt.py) |
| `LocalContextMiddleware` — git branch, status, a depth-limited tree | [context.py](../agent/deep/context.py) |
| `ShellAllowListMiddleware` | [shell.py](../agent/deep/shell.py) |

Three parts are **adapted rather than copied**, and each adaptation is a fact
about this pool rather than a preference:

- **Identity is a pool, not a model.** dcode writes *"You are running as model
  X, your context window is N tokens"* because a run has one model. Here the
  router picks per call and a session is routinely served by four or five
  models, so naming one is false by the second step. The prompt states the
  *floor* every eligible member clears, which is the part that stays true.
- **Headless always.** dcode defaults to an interactive TUI where the agent may
  ask and wait. Nobody is watching a session here, so `ask_user` is never
  installed and the prompt takes the branch that says assume and proceed
  ([design note §3, R3](design/long-run-harness.md#3-what-helpful-requires-draft--v3)).
- **Paths root at the jail.** dcode runs `virtual_mode=False` and tells the
  model to build absolute host paths. This backend's `/` *is* the workdir
  ([6.2](#62-the-blast-radius)), so copying that instruction verbatim would fail
  every tool call.

Not carried over: human-in-the-loop approval, auto mode, cost tracking, MCP, the
code interpreter, the TUI. Worth revisiting: skills, memory — dcode's `AGENTS.md`
convention is the same idea as this project's `NOTES.md`
([6.3](#63-the-agents-instructions)) — and the rubric self-grader, which belongs
to the evaluation question rather than this one.

**The context section earns its place.** Without it the first thing any agent
does is spend two or three calls discovering the shape of the project, and those
are the most expensive calls in a run because nothing has been compacted yet.
Adding it took a measured run from 21 model calls to 14 and removed every `glob`
call. Unlike dcode it is built once into the prompt rather than injected per
call: the tree barely moves inside a run, and on a pool where each step spends a
request against a daily quota, re-sending it buys nothing.

---

**Previous:** [← 5. Providers and limits](05-providers.md) · **Next:** [7. Observability →](07-observability.md)
