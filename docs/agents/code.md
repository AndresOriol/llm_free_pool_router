[← Wiki index](../README.md)

# The coding agent

*One conversation over the pool and the host shell, configured the way
`deepagents-code` configures one — what it can do, what it costs, and the arm
that was deleted to get here.*

## One conversation, on the pool

[agent/code/](../../agent/code/). A **session**: one long unattended run over one
project, task on stdin, process exits at EOF, every model call routed through the
pool ([Failover](../pool/failover.md)).

```bash
python -m agent.code ../my-project --task "..."    # or: < brief.md
```

`workdir` is the project. The task arrives on `--task` or stdin and the process
exits when it is done, which is what makes it drivable from a script or from an orchestrating
agent ([Driving the free agents](../operations/driving-agents.md)). Its
counterpart, `python -m agent.explore`, takes the same shape and researches the
web instead ([The web explorer](explore.md)).

A call holds the conversation, compacted by the SDK when it grows, and the
session runs only on pool members holding at least **128,000 input tokens** —
declared as a hard floor, not a preference ([The pool drops in with no adapter](#the-pool-drops-in-with-no-adapter)).

### The arm that was deleted

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
([The cost result is the one that replicated](#the-cost-result-is-the-one-that-replicated)). That result was never
overturned. It was taken before the SDK shipped summarization, message eviction
and tool-output offloading, and against a pool whose floor for this work was
6,000 tokens rather than 128,000 — so its inputs no longer hold — but nobody has
re-run it to a conclusion.

**So this was a decision, not a finding.** Two architectures cost roughly twice
the maintenance of one, every change had to be made and evaluated in both, and
the conversational arm is the one whose loop, compaction and tooling come from a
maintained SDK rather than from this repo. The cost gap is the standing risk that
choice accepts, and `tokens_in` per run is the metric to watch for it
([Automatic metrics](../evaluation/metrics.md#automatic-metrics)).

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

**What left with it, and what came back.** The narrow-role session read a
project's `NOTES.md`, appended its own account to it, journalled every step so a
crash could resume, and committed incrementally on its own branch. This agent
commits (it holds `git`), writes its account into `NOTES.md` (`code-account`,
promoted), and commits a handover when its budget runs out
([The step budget](#the-step-budget)). The crash journal has not come back
([Status and roadmap](../status.md#the-two-phases-of-the-project)).

## The blast radius

**There is no jail.** The coding agent runs on deepagents'
[`LocalShellBackend`](../../agent/code/agent.py), whose `execute` is
`subprocess.run(shell=True)` on the host. Anything the user account running the
harness can do, an unattended session can do: install packages, reach the
network, delete files anywhere on the disk, push to a remote. Read that sentence
before pointing this at a machine you care about.

`virtual_mode=True` is still set, and it is worth being precise about what it
does. It roots the **file tools** at the workspace — `read_file`, `write_file`,
`edit_file`, `glob` and `grep` treat `/` as the workdir and refuse `..`. It does
nothing to `execute`, which is why
[`prompts/working_dir.md`](../../agent/utils/prompts/working_dir.md) now describes
the two separately instead of claiming `/` is all the agent can reach. A prompt
that overstates the boundary is worse than one that states none: the model plans
around a rule the harness will not enforce.

It also names the shell `execute` hands a command to (`shell()` in
[prompts.py](../../agent/utils/prompts.py)): `cmd.exe` on Windows, `/bin/sh`
elsewhere. Left to guess, the model assumed POSIX on a Windows host, and 30% of
the `execute` calls in the traced eval runs were Python subprocess wrappers
working around that guess.

So staying inside the project is a **convention the agent is asked to keep**,
not a boundary it is held to. The prompt asks; nothing checks.

### Why the restrictions went

Earlier versions of this harness shipped `RestrictedShellBackend`: an allowlist
of `python`/`pytest`/`git`, `shell=False` so no metacharacter was ever
interpreted, git filtered by subcommand, secrets stripped from the child
environment. It was ~360 lines with its own tests, and it is gone.

The case against it is the one it made against itself. `python` was on the
allowlist because the agent has to run the tests it writes — and `python -c
"subprocess.run([...])"` is a complete escape from every other rule, which
agents found immediately. The allowlist stopped the careless command and never
the determined one, while costing real work: a run spent 900 of its 1,050
seconds on heredocs the backend had to refuse, and the `python -c` wrapper
exits 0 whatever the child did, so `execute` reported a **failing** build as
`[Command succeeded with exit code 0]` and a session called its own broken
verification gate passing.

A boundary that stops nothing an attacker would do, while shaping how the agent
works around it, is not a boundary. It is a tax. **The isolation was always a
container's job**, and naming that plainly is more honest than a list that read
like a policy.

What went with it: `agent/utils/backend.py`, `agent/utils/shell.py` and its
`ShellAllowListMiddleware`, the `HARNESS_SHELL=1` opt-in (now the only mode),
the `{running_commands}` prompt sections, and two test files.

Two Windows fixes went with them, and both failure modes are live again:

- `FilesystemBackend._resolve_path` compares a resolved path against a root
  resolved once at startup. On Windows `Path.resolve()` cannot strip the `\\?\`
  extended-length prefix while another process holds the file open, so a write
  **inside** the workspace is intermittently refused as an escape. This killed a
  session after three successful writes to the same directory.
- `LocalShellBackend` decodes child output with `text=True`, which is the
  platform codepage. On Windows that is cp1252, whose strict decode raises on
  `0x81`, `0x8d`, `0x8f`, `0x90` and `0x9d` — so a traceback or a `git diff`
  carrying one of those bytes raises inside `execute()` instead of returning
  output.

Both are upstream bugs rather than missing restrictions. If either starts
costing runs, the fix is a narrow subclass or a patch upstream, not the
allowlist coming back.

### The step budget

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
([Metrics](../evaluation/metrics.md)): a loop repeats itself, a spent budget does not.
The reserve exists because a run that cannot see its own budget cannot choose
to land its work before it goes — being told is the only thing that lets it.

## What failover looks like in practice

Behind the 128,000-token floor only Gemini members serve a coding session, so a
step that meets a rate limit walks the other Gemini accounts and models before
one accepts. That is the design working — but it is also why the pool drains
fast, why a run so often ends up on the lite tier
([Status and roadmap](../status.md#where-things-stand)), and why
`failover_bounces` is a first-class metric
([Automatic metrics](../evaluation/metrics.md#automatic-metrics)).

Free tiers signal limits inconsistently — Groq uses HTTP 429 *and* 413 for
tokens-per-minute — and both are transient
([Classifying a failure](../pool/failover.md#classifying-a-failure)). A model the platform has
retired is not: it comes back as a 404, and the router drops that member from
the pool for the rest of the process rather than dying on it. A 503 is the
model's capacity rather than the key's, so it benches that model on every
account at once ([Failover](../pool/failover.md)).

## Why it is shaped this way

Four findings from the runs of the deleted narrow-role arm, on the one L0
scenario. The configurations are in `git log`; these are what they showed, and
the last three still shape how any result here is read.

### The cost result is the one that replicated

Same task, same pool, interleaved: **226,854 input tokens** through a
conversational loop against **5,756** through narrow roles, stable across every
rep and every batch, with between-configuration spread far exceeding
within-configuration variance. Provider calls and failover bounces moved the
same way. It is the result the deleted arm rested on, and the one its deletion
accepted ([The arm that was deleted](#the-arm-that-was-deleted)).

### The pass column is noise

One configuration scored **3/3 in one batch and 1/3 in the next**, unchanged,
hours apart. The noise floor is around 15 points at ten times these sample sizes
([Fair comparison](../evaluation/method.md#fair-comparison)). Cost metrics replicate;
pass rates do not. Any ranking read off a pass column at n<10 is invented, and
this project has had to retract one.

### Every failure is `reasoning`

**12 of 13 recorded failures**, with zero `retrieval` and zero `tooling`. Every
configuration found the file, edited it, and ran the tests — and was
conceptually wrong. No change of topology moves that, which is why more
architecture work was not the next move and scenario authoring was
([What to do next](../status.md#what-to-do-next)).

A taxonomy where one class holds 92% of the mass is a rename of "failed", not a
diagnosis. Subdividing it is the standing job of the batch analysis
([design note §6.3](../design/long-run-harness.md#63-why-the-existing-taxonomy-needs-subdividing-urgently)).

### Closing the loop is not the same as being right

The textbook instance, from a run whose own tests passed:

```diff
-    value = headers.get("retry-after")
+    value = headers.get("Retry-After")
```

The scenario is about **case-insensitive** lookup. The agent flipped the case to
satisfy the test it could see; the hidden set, which checks other casings,
failed it. This is what the withheld-test design exists to catch
([Anatomy](../evaluation/scenarios.md#anatomy)), and it is not a token-budget problem. **A
cheaper agent reaches this failure mode sooner, not later.**

## What makes it a coding agent

[agent/code/](../../agent/code/). Almost none of this is agent design.
`create_deep_agent` already assembles the todo list, the filesystem tools, the
subagent `task` tool and summarization, and the `execute` tool switches itself
on because `LocalShellBackend` satisfies `SandboxBackendProtocol`. Two things
are worth knowing.

### The pool drops in with no adapter

`resolve_model` returns a `BaseChatModel` unchanged, and `RouterChatModel` is
one. So the pool is passed where a model id would go, and every failover
guarantee on [Failover](../pool/failover.md) holds inside a harness this repo did
not write. That is the whole integration.

The floor is enforced, not preferred. `for_context(128_000, strict=True)` makes
the router *refuse* to route below it and wait for a wide member to leave
cooldown, rather than falling back to a narrow one
([Size-aware selection](../pool/failover.md#size-aware-selection)). Selection and the cooldown wait
have to be asked the same question — a wait computed over the whole pool reports
"someone is free" because a Groq member is warm, and the run dies with a wide
member seconds from returning.

### The configuration, ported from dcode

The configuration is ported from `deepagents-code`'s `create_cli_agent` (MIT):
the generated system prompt, the project overview put in front of the model, and
the same backend it runs on.

| Ported | Where |
| --- | --- |
| System prompt: understand → build → test → verify; match the spec exactly; parallel tool calls; paginated reads; git safety; root-cause debugging; stop after three identical failures | [prompts/system.md](../../agent/code/prompts/system.md) |
| Prompt assembly and its interpolated sections | `template_values` in [agent.py](../../agent/code/agent.py), and [agent/utils/prompts/](../../agent/utils/prompts/) for the sections every agent shares |
| `LocalContextMiddleware` — git branch, status, a depth-limited tree | `project_section` in [agent.py](../../agent/code/agent.py) |
| `LocalShellBackend` | `local_shell` in [agent.py](../../agent/code/agent.py), with `virtual_mode=True` so `/` is the workspace |

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
  ([design note §3, R3](../design/long-run-harness.md#3-what-helpful-requires-draft--v3)).
- **Paths root at the jail.** dcode runs `virtual_mode=False` and tells the
  model to build absolute host paths. This backend's `/` *is* the workdir
  ([The blast radius](#the-blast-radius)), so copying that instruction verbatim would fail
  every tool call.

Not carried over: human-in-the-loop approval, auto mode, cost tracking, MCP, the
code interpreter, the TUI. Skills ([Skills](#skills)) and memory
([The project's own memory file](#the-projects-own-memory-file)) have since been picked up. Worth
revisiting: the rubric self-grader, which belongs to the evaluation question
rather than this one.

**The context section earns its place.** Without it the first thing any agent
does is spend two or three calls discovering the shape of the project, and those
are the most expensive calls in a run because nothing has been compacted yet.
Adding it took a measured run from 21 model calls to 14 and removed every `glob`
call. Unlike dcode it is built once into the prompt rather than injected per
call: the tree barely moves inside a run, and on a pool where each step spends a
request against a daily quota, re-sending it buys nothing.

### What each call carries

What the model sees is assembled from a few places, and knowing which is how to
change what the agent does. [agent.py](../../agent/code/agent.py) builds it and
reads top to bottom; everything the model reads is Markdown.

| | where it comes from |
| --- | --- |
| system prompt | [prompts/system.md](../../agent/code/prompts/system.md), its `{placeholders}` filled once, at the start: the sections every agent shares ([agent/utils/prompts/](../../agent/utils/prompts/) — headless, the pool's identity, the jail's `/`, what `execute` runs), `contradicted_requests.md` and `project_notes.md` unless switched off, the project section, and the agents it may run ([Delegation](delegation.md)) |
| framework sections and tool schemas | deepagents' own, less its base prompt and todo section: the file tools, `execute`, `task`; `write_todos` is described by [tool_descriptions/write_todos.md](../../agent/code/tool_descriptions/write_todos.md), on the main agent and the `general-purpose` sub-agent alike |
| skills | one line each — name and description — from [skills/](../../agent/code/skills/); the body is read on demand ([Skills](#skills)) |
| conversation | the task, then every tool call and its result; the SDK summarizes it when it grows too long |
| wrap-up | [prompts/wrap_up.md](../../agent/code/prompts/wrap_up.md), sent as a message only when the step budget runs out |
| what outlives it | the repository — the files on disk and the commits |

`AGENT_INVARIANT_GUARD=0` and `AGENT_WRITE_ACCOUNT=0` each leave their section
out; both are on by default, so each is a configuration an A/B can measure.

Unlike the explorer's, no framework tool is taken away
([The surface is chosen, not inherited](explore.md#the-surface-is-chosen-not-inherited)): a coding
agent does explore a repository, and does run programs. One is re-described.
Upstream's `write_todos` says when a list is worth keeping and never what an
item is, and the traced runs filled it with the prompt's own phases — "read and
understand", "implement the requested changes", "run tests" — then ticked them
in one update at the end. In one run "update documentation" was ticked after a
read, and the doc its hidden test checks was never edited. The agent's own
description says an item is a requirement naming the file it changes, and that
it is closed only by evidence in the conversation. A harness profile cannot
reach that tool, so it goes through `FrameworkSurface`
([surface.py](../../agent/utils/surface.py)).

The same middleware cuts two framework sections (`PRUNED_SECTIONS` in
[agent.py](../../agent/code/agent.py)). deepagents appends `BASE_AGENT_PROMPT`
after the agent's own prompt, so the model was reading two "Doing Tasks"
sections that disagreed, "the user can see your responses in real time", and
"ask for guidance" when blocked, none of which is true of a headless run. The
todo list's section went with the tool description it contradicted. The rule
is narrower than the explorer's: prose that contradicts this agent's prompt is
cut, and the sections describing tools it does have — files, `execute`, `task`
— stay.

## Skills

A **skill** is a procedure the agent reads *when it is about to do that kind of
work*: a directory under [agent/code/skills/](../../agent/code/skills/) holding a
`SKILL.md` with a name, a description, and a body. deepagents'
`SkillsMiddleware` is what installs them — the same mechanism `deepagents-code`
points at `.claude/skills`, pointed at a directory of this project's own.

**It costs no tool.** The middleware adds a prompt section listing each skill's
name and description; the body is an ordinary `read_file` away. That matters
here for the reason everything does: tool schemas are 91% of what a step spends
([Why it is shaped this way](#why-it-is-shaped-this-way)), and a schema is charged on every step of
every run whether the skill is ever used or not. A sentence of description is
not.

### The description is the gate

Progressive disclosure only works if the model can tell, from the description
alone, that the body is worth reading. That makes the description the load-bearing
part: it is the only thing in front of the model on every call, and a body the
agent never opens is prose that does nothing.

So `delegate`'s description does not describe the skill. It states the trigger —
*before you run any `python -m agent.*` command, and whenever a task needs work
you cannot do with your own tools* — names the agents this run can actually
reach, and says when **not** to open it. What is inside (each command, what it
leaves on disk, how to write a brief) stays inside.

### Rendered per run, not committed

Who can be reached is probed at start-up: `explore` is offered only if the
search pool answers, `scenarios` only if that repository is on disk
([A capability is a fact to probe, not to infer](explore.md#a-capability-is-a-fact-to-probe-not-to-infer)). A
committed file cannot say that. So `skills/delegate/SKILL.md` is a template with
`{description}` and `{roster}` in it, and `render_skills()` fills it into a
temporary directory that lives exactly as long as the agent does.

With no peers reachable, nothing is rendered, nothing is mounted, and the
middleware is not installed. That is what keeps `AGENT_PEERS=` an arm with *no
delegation in it* rather than one that reads how to delegate and then fails —
the baseline prompt is byte-for-byte what it was before skills existed.

### Why the directory is mounted

The middleware prints a path and the model reads it. The agent's `/` is the
workspace it was given, which is usually not this repository, so an absolute
path to `agent/code/skills/` is one it cannot read.

So the backend becomes a `CompositeBackend`: the workspace jail as before, plus
one route mapping `/skills/` to the rendered directory. `execute` is not
path-routed — the composite always runs it on the default backend — so what the
agent may *run*, and where, is untouched. What changed is that four file tools
can also read one small directory of generated Markdown.

### What belongs in a skill, and what does not

`prompts/` and `skills/` differ in *when* the model reads them, and that is the
whole test.

| | read | holds |
| --- | --- | --- |
| a prompt section | every call | what is always true |
| a skill description | every call | when to open the body, and when not to |
| a skill body | when the agent judges it applies | a procedure most runs never need |

`delegate` splits along that line
([The roster and the how-to are a skill](delegation.md#the-roster-and-the-how-to-are-a-skill)): what
survives in `system.md` is one sentence sending the agent to the skill; the
roster, the cost, the brief and how to read what comes back are all in the body.

Two things about the framework's own text. Its default skills prompt is ~1,500
characters explaining progressive disclosure, and it tells the model that some
sources are shared with other agent tools on the machine, which is not true
here — so it is replaced by
[prompts/skills.md](../../agent/code/prompts/skills.md), a prompt file like every
other. And the frontmatter is parsed as YAML: a description full of colons and
backticks has to be a block scalar, or the skill is dropped from the listing
without an error.

### One skill does not pay for itself yet

Measured on the system prompt, with `explore` reachable:

| | no peers | with peers |
| --- | --- | --- |
| before | 24,299 | 25,273 |
| after | 24,300 | 26,337 |

The delegation prose that left `system.md` was ~970 characters; the mechanism
that replaced it — the framing section, the locations line, and a description
long enough to be a real gate — costs ~2,000. **This is a bet that there will be
a second and third skill**, not a saving. It turns over when the bodies kept out
of the prompt exceed the framing that keeps them out, and `tokens_in` per run is
what says whether it did ([Metrics](../evaluation/metrics.md)).

## `read_file` reads the whole file

The tool every agent here reads with is
`read_file(file_path, from_line=None, to_line=None)`, and with neither bound it
returns the file. deepagents ships it the other way round — 100 lines, with a
description that teaches scan-then-page — and
[agent/utils/file_tools.py](../../agent/utils/file_tools.py) is where that is
changed, for all three agents at once.

### A default sized for the wrong scarce thing

Paginating protects a context window. That is the right instinct when context
is what you are short of, and it is the wrong one here twice over.

A session routes only to members holding at least 128,000 input tokens
([pool.py](../../agent/utils/pool.py)). Reading a 400-line file in four pages to
protect a floor we set that high is caution against a limit we already bought
our way past. And it is not free: what actually runs out on free tiers is
*requests per day* ([Skipping a member whose day is spent](../pool/failover.md#skipping-a-member-whose-day-is-spent)),
so three extra reads are three calls that could have been three edits — each one
re-sending the whole conversation to fetch a hundred lines.

The ceiling did not go away, it moved. The middleware still caps one tool result
at 20,000 tokens and appends a truncation notice, and the jail still refuses
files over 10 MB. What changed is that truncation is now the *exception* a large
file hits, instead of the rule every file hits.

### The arguments are the numbers on the screen

`offset`/`limit` is a 0-indexed start plus a count, in front of a tool whose
output is `cat -n` — 1-indexed. A model that reads `  148  def run(` and wants
to start there has to ask for `offset=147`. That subtraction is a step it can
get wrong, and getting it wrong does not look like an error: it looks like a
confident answer about the line above the one you meant.

`from_line`/`to_line` are inclusive and are the numbers the tool just printed.
There is no arithmetic left to get wrong.

A reversed or zero bound comes back as an error *message* rather than an
exception, for the same reason a refused command does
([The blast radius](#the-blast-radius)): langgraph re-raises a `ValueError` out of the tool
node, so a model that swapped two arguments would end the session instead of
correcting itself on the next step.

### Where this breaks

deepagents exposes no setting for any of it — `FilesystemMiddleware.__init__`
takes no read-length argument and no per-tool override — so this is a rebind of
three private names at import: the tool factory, wrapped; and the two pieces of
prose that would otherwise describe a tool that no longer exists.

That is three seams an upgrade can break, and two of the three break *silently*:
the model would simply be told again that it reads 100 lines and takes an
`offset`. So the description patch is anchored to deepagents' exact wording, a
miss logs a warning rather than crashing a run, and
[tests/agent/test_read_file.py](../../tests/agent/test_read_file.py) asserts every
seam still holds — including that the anchor still appears in the *installed*
package. An upgrade fails in CI instead of quietly halving what the agent reads.

**Unmeasured.** The argument above is arithmetic about requests, not a result.
The number that would settle it is calls-per-run at equal pass rate
([Metrics](../evaluation/metrics.md)); nothing has been run across this change yet.

## The project's own memory file

The agent is jailed to a workspace that is almost never this repository, and a
project that has been worked on before has usually written down what it expects
of whoever changes it: the command its tests are run with, the convention its
files follow, the directory that is vendored and must not be edited. That file
is called `AGENTS.md` (<https://agents.md>) or, where it was written for Claude
Code, `CLAUDE.md`.

Nothing made the agent read it. It could discover one — `ls` returns it, and a
model that opens it is a model that spent two calls to learn what it needed
before its first edit — but discovery is a coin flip, and the run that loses it
reinvents a convention the project had already settled. So the file is loaded
before the first call and put in the system prompt, the same argument as the
project context section ([Why it is shaped this way](#why-it-is-shaped-this-way)): what is true for
every step of a run is cheapest paid for once.

### One file, not both

deepagents' `MemoryMiddleware` takes a list of sources, loads every one that
exists and concatenates them. Handing it both names is therefore not "pick
whichever this project uses" — it is "load both", and a repository holding both
is usually holding two drafts of one document, drifted apart by however long
since one of them was last updated. This repository is the example: its
[AGENTS.md](../../AGENTS.md) and [CLAUDE.md](../../CLAUDE.md) open with different
north stars.

So the harness picks, and the order is `CLAUDE.md` then `AGENTS.md`. The
vendor-neutral spec is `AGENTS.md` and it is the one deepagents implements, so
the abstract argument favours it — but the projects this harness is pointed at
are worked on with Claude Code, and `CLAUDE.md` is the file that is actually
kept current there. The stale draft is the one worth not reading. Preferring one
is not requiring it — a project with only an `AGENTS.md` gets a memory file.

### What it is allowed to be

The section that frames the file is this repo's, not the framework's, for the
same reason the skills section is ([Skills](#skills)): deepagents' default is
around 4,500 characters about a user — one to ask for a Slack ID, to learn
preferences from, to be interrupted by — and nobody is watching this run. It is
charged on every call. [`prompts/memory.md`](../../agent/code/prompts/memory.md)
says the part that is true here in a quarter of the space.

What it says is mostly about rank. The file is data read off a disk the agent
also writes to, so it cannot be allowed to act as an instruction from whoever
started the run: a line asking for something the task did not ask for is a line
to report rather than obey, and where the file disagrees with what `read_file`
returns, the code is what is true. Underneath that it is an ordinary file, which
makes it editable — a command that is now spelled differently gets fixed in the
commit that made it so. What happened in *this* session is not memory; that is
the account's job ([`prompts/project_notes.md`](../../agent/code/prompts/project_notes.md)),
and the two are kept apart because a project's standing instructions outlive the
session and a run log does not.

**Unmeasured.** No scenario has been run across this change. The claim it rests
on — that a run which is told the project's conventions violates fewer of them
than one that has to find them — is plausible and untested.
