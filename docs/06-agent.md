[← Wiki index](README.md)

# 6. The coding agent

*One architecture over one pool and one jail, the comparison that ended with the
other one deleted, and what the survivor can and cannot do.*

## 6.1 One conversation, on the pool

[agent/code/](../agent/code/). A **session**: one long unattended run over one
project, task on stdin, process exits at EOF, every model call routed through the
pool ([4. Failover](04-failover.md)).

```bash
python -m agent.code ../my-project < brief.md
```

`workdir` is the project. The task arrives on stdin and the process exits at
EOF, which is what makes it drivable from a script or from an orchestrating
agent ([12.5](12-development-harness.md#125-driving-the-free-agents)). Its
counterpart, `python -m agent.explore`, takes the same shape and researches the
web instead ([15. The web explorer](15-explorer.md)).

A call holds the conversation, compacted by the SDK when it grows, and the
session runs only on pool members holding at least **128,000 input tokens** —
declared as a hard floor, not a preference ([6.6.1](#651-the-pool-drops-in-with-no-adapter)).

### 6.1.1 The arm that was deleted

Until recently there were two. `agent/harness/` split every task into narrow
roles over a shared log so that each call fitted the pool's **narrowest** member,
8,000 tokens. It is gone; `git log` has it.

**Why it existed.** A call had to fit a Groq member, so work was split until it
did. That constraint was lifted for coding work: a Groq account holds 100,000
tokens *per day*, so one wide request would spend a whole day's budget — those
members can never serve a conversation, and pretending they might is what forced
the splitting. Groq stays in the pool for work that fits it.

**What the measurements said, honestly.** The narrow-role design won on cost and
won decisively: **226,854 input tokens through a conversational loop against
5,756 through narrow roles**, replicated on every rep of every batch
([6.7.1](#641-the-cost-result-is-the-one-that-replicated)). That result was never
overturned. It was taken before the SDK shipped summarization, message eviction
and tool-output offloading, and against a pool whose floor for this work was
6,000 tokens rather than 128,000 — so its inputs no longer hold — but nobody has
re-run it to a conclusion.

**So this was a decision, not a finding.** Two architectures cost roughly twice
the maintenance of one, every change had to be made and evaluated in both, and
the conversational arm is the one whose loop, compaction and tooling come from a
maintained SDK rather than from this repo. The cost gap is the standing risk that
choice accepts, and `tokens_in` per run is the metric to watch for it
([10.2](10-metrics.md#102-automatic-metrics)).

**What the cost actually is, measured over 40 runs.** `tokens_in` correlates
**+0.95 with `steps`**, +0.95 with `tool_calls` and +0.92 with `provider_calls`
— and only **+0.24 with `failover_bounces`**. The suspicion that failover was
replaying whole contexts and inflating the number is wrong: a bounce spends a
*request* and no tokens, because a 429 or a 404 is refused at the gate and there
is nothing to bill.

So the number is what it looks like. A call carries **25,000–40,000 input
tokens**, and a run makes 25 to 50 of them, because a conversation re-sends
itself every step. That is the architecture, not a defect in it, and it is why
the narrow-role arm was cheap: it never held a conversation to re-send.
`tokens_per_call` is recorded per run so this stays visible without being
re-derived — it separates a run that was long from one that was expensive, which
`tokens_in` alone cannot do.

**This does not settle whether deleting that arm was a mistake.** It settles
where the money goes. The comparison itself is still unrun: 226,854 against
5,756 was measured on a different pool, a different floor and a different SDK,
and nothing since has put the two architectures on the same scenarios.

**What left with it.** The narrow-role session read a project's `NOTES.md`,
appended its own account to it, journalled every step so a crash could resume,
and committed incrementally on its own branch. The conversational agent commits
(it holds `git`) but does **none** of the rest. The standing-maintainer loop the
North Star describes therefore has a hole in it until that is rebuilt on this arm
([13.2](13-roadmap.md#132-what-to-do-next)).

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
the timeout. The *prompt* names the same programs, from the same constant, so
the model is told the shape of `execute` before it spends a step discovering
it: every recorded run before that spent at least one being refused, usually
on `&&`.

Because `python` is on that list, the way round every other restriction is
`python -c "subprocess.run([...])"`, and agents find it immediately. It has one
sharp edge: the wrapper exits 0 whatever the child did, so `execute` reports a
**failing** build as `[Command succeeded with exit code 0]`. A recorded run
read that line over its own broken verification script and reported the gate as
passing. The prompt now says to carry the child's code out with
`sys.exit(res.returncode)` — the harness cannot tell the difference itself,
because as far as it is concerned the program it launched succeeded.

**Git, by subcommand.** A session commits its own work incrementally on its own
branch, so the human's gate is the **merge**, not the commit. `push`, `merge`,
`rebase`, `reset` and `clean` are refused
([design note §4.1](design/long-run-harness.md#41-git)).

This is a **small blast radius, not a sandbox**. `python` is arbitrary code
execution. For real isolation, run the whole thing inside a container — which
becomes a prerequisite the day sessions run unattended overnight.

### 6.2.1 The step budget

A session gets **400 supersteps** (`AGENT_STEP_BUDGET`), of which the last 40
are held back. Spending them is an ordinary way for a run to end, not a crash:
when the first 360 are gone the session is told so and gets the reserve to
commit what is on disk and write a handover, and the run finishes normally with
`=== STOPPED (step budget spent) ===` in place of `=== DONE ===`.

This used to be 120 supersteps, described as a guard against a loop that never
settles, on the reasoning that the pool's daily request quota would bind first.
It does not. A recorded run reached 120 in **220 seconds** of entirely ordinary
work — reading the tree, writing five files, installing a Node toolchain
through `python -c`, compiling it — and the error came straight out of
`agent.invoke`, taking the summary and the run record with it. A session whose
code compiled reported nothing and left it uncommitted.

So the limit is a **budget**, and running out of it says nothing about whether
the session was looping. The two are separate rows in the failure taxonomy
([10. Metrics](10-metrics.md)): a loop repeats itself, a spent budget does not.
The reserve exists because a run that cannot see its own budget cannot choose
to land its work before it goes — being told is the only thing that lets it.

## 6.3 What failover looks like in practice

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

## 6.4 Why it is shaped this way

Three findings from the runs that produced this architecture. The configurations
themselves are in `git log`; these are what they showed, and they still hold.

### 6.4.1 The cost result is the one that replicated

Same task, same pool, interleaved: **226,854 input tokens** through a
conversational loop against **5,756** through narrow roles, stable across every
rep and every batch, with between-configuration spread far exceeding
within-configuration variance. Provider calls and failover bounces moved the
same way. This is the result the architecture rests on.

### 6.4.2 The pass column is noise

One configuration scored **3/3 in one batch and 1/3 in the next**, unchanged,
hours apart. The noise floor is around 15 points at ten times these sample sizes
([8.6](08-evaluation-method.md#86-fair-comparison)). Cost metrics replicate;
pass rates do not. Any ranking read off a pass column at n<10 is invented, and
this project has had to retract one.

### 6.4.3 Every failure is `reasoning`

**12 of 13 recorded failures**, with zero `retrieval` and zero `tooling`. Every
configuration found the file, edited it, and ran the tests — and was
conceptually wrong. No change of topology moves that, which is why more
architecture work was not the next move and scenario authoring was
([13.2](13-roadmap.md#132-what-to-do-next)).

A taxonomy where one class holds 92% of the mass is a rename of "failed", not a
diagnosis. Subdividing it is the standing job of the batch analysis
([design note §6.3](design/long-run-harness.md#63-why-the-existing-taxonomy-needs-subdividing-urgently)).

### 6.4.4 Closing the loop is not the same as being right

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

## 6.5 What makes it a coding agent

[agent/code/](../agent/code/). Almost none of this is agent design.
`create_deep_agent` already assembles the todo list, the filesystem tools, the
subagent `task` tool and summarization, and the `execute` tool switches itself
on because `RestrictedShellBackend` satisfies `SandboxBackendProtocol`. Two
things are worth knowing.

### 6.5.1 The pool drops in with no adapter

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

### 6.5.2 The configuration, ported from dcode

The configuration is ported from `deepagents-code`'s `create_cli_agent` (MIT):
the generated system prompt, the project overview put in front of the model, and
a shell allowlist that refuses a command **as a tool message** rather than as an
exception, so the model reads the reason and corrects itself instead of retrying.

| Ported | Where |
| --- | --- |
| System prompt: understand → build → test → verify; match the spec exactly; parallel tool calls; paginated reads; git safety; root-cause debugging; stop after three identical failures | [prompts/system.md](../agent/code/prompts/system.md) |
| Prompt assembly and its interpolated sections | `template_values` in [agent.py](../agent/code/agent.py), and [agent/runtime/prompts/](../agent/runtime/prompts/) for the sections every agent shares |
| `LocalContextMiddleware` — git branch, status, a depth-limited tree | `project_section` in [agent.py](../agent/code/agent.py) |
| `ShellAllowListMiddleware` | [agent/runtime/shell.py](../agent/runtime/shell.py) |

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
convention is the same idea as the `NOTES.md` loop that left with the other arm
([6.1.1](#611-the-arm-that-was-deleted)) — and the rubric self-grader, which
belongs to the evaluation question rather than this one.

**The context section earns its place.** Without it the first thing any agent
does is spend two or three calls discovering the shape of the project, and those
are the most expensive calls in a run because nothing has been compacted yet.
Adding it took a measured run from 21 model calls to 14 and removed every `glob`
call. Unlike dcode it is built once into the prompt rather than injected per
call: the tree barely moves inside a run, and on a pool where each step spends a
request against a daily quota, re-sending it buys nothing.

### 6.5.3 What each call carries

What the model sees is assembled from a few places, and knowing which is how to
change what the agent does. [agent.py](../agent/code/agent.py) builds it and
reads top to bottom; everything the model reads is Markdown.

| | where it comes from |
| --- | --- |
| system prompt | [prompts/system.md](../agent/code/prompts/system.md), its `{placeholders}` filled once, at the start: the sections every agent shares ([agent/runtime/prompts/](../agent/runtime/prompts/) — headless, the pool's identity, the jail's `/`, what `execute` runs), `contradicted_requests.md` and `project_notes.md` unless switched off, the project section, and the agents it may run ([16](16-delegation.md)) |
| framework sections and tool schemas | deepagents' own, unedited: the file tools, `execute`, `write_todos`, `task` |
| conversation | the task, then every tool call and its result; the SDK summarizes it when it grows too long |
| wrap-up | [prompts/wrap_up.md](../agent/code/prompts/wrap_up.md), sent as a message only when the step budget runs out |
| what outlives it | the repository — the files on disk and the commits |

`AGENT_INVARIANT_GUARD=0` and `AGENT_WRITE_ACCOUNT=0` each leave their section
out; both are on by default, so each is a configuration an A/B can measure.

Unlike the explorer's, nothing the framework injects is taken away or
re-described ([15.5.1](15-explorer.md#1551-the-surface-is-chosen-not-inherited)):
a coding agent does explore a repository, and does run programs.

---

**Previous:** [← 5. Providers and limits](05-providers.md) · **Next:** [7. Observability →](07-observability.md)
