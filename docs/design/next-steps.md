This document is used to annotate ideas of improvement that can be later used by tha coding agent.

## Improving the router

I feel that it would be better to build a more robust router that would led by its configuration to select a model or some models, so you can have more control over what you are using.
Improve the rerouting so that there are less misses when there is a lot of traffic for a model.

### Optional (high complexity)

Create an automatic classifier that selects the best model depending on the task and current availability of the pool. This implementation only makes sense if the previous 2 themes are already implemented. This improvement by itself can be considered an independent project.

## What the deepagents examples do that we do not

From a review of <https://github.com/langchain-ai/deepagents/tree/main/examples>
against our three agents. Two of those examples are systems this repo arrived at
independently, which makes the places they differ worth taking seriously rather
than treating as a matter of taste.

### Split the eval set into train and holdout

`better-harness` is `agent/improve` + `evals` built by the library's authors: an
outer agent edits an inner agent's surfaces, and **a change is kept only if the
combined pass count on a *train* and a *holdout* split improves**.

We have no holdout. `check_issue` replays an issue's signature against
whatever runs exist, and `delegate_fix` gates on staleness rather than on a
score. That is how a harness change overfits the handful of scenarios it was
diagnosed from — and we already know the pass column is noise at these sample
sizes ([The pass column is noise](../agents/code.md#the-pass-column-is-noise)), so the risk is
not theoretical. This is the most valuable thing in the examples for us.

*Decision implemented:* Hold out scenarios by topic, not runs. Declared in `/evals/splits.yaml`. See the new section in `/docs/evaluation/method.md`. A pass count at these sample sizes is weak evidence and the gate is a floor, not a proof.


### Declare the editable surfaces

`better-harness` names what an outer agent may edit, as data: a prompt, a tool
file, a skill, a middleware, each loaded either by patching a module attribute
(`package.module:ATTRIBUTE`) or by replacing a file in the workspace for one
eval run.

Our issues carry a free-text `lever` — a path, written by the model, that
nothing checks. A declared surface would make `_staleness` a lookup instead of a
git archaeology pass ([The improvement agent](../agents/improve.md)), and would let a
delegation be refused for naming something that is not editable.

### Read `ralph_mode` before building more of the long-run harness

Fresh context each iteration, the filesystem and git as the only memory. That is
the shape [long-run-harness.md](long-run-harness.md) describes, already written
down by someone else. Read the two side by side before adding to ours.

### Evaluate `deepagents-cli`, do not assume it fits

`run_non_interactive` handles model resolution, tool registration,
checkpointing, streaming and HITL approval — most of what our three
`__main__.py` do. But it is **not installed and not a declared dependency**, and
model resolution is the pool's whole job here, so the part that would carry the
most weight is the part that fits worst.

## Our agents as Claude Code subagents, through Claude Mods

*Benched 2026-09-19. Come back once Mods leave early access.*

**The goal.** Run `agent/code`, `agent/explore` and `agent/improve` as Claude Code subagents. While
one of them works, its tool calls should be visible live in Claude Code, the way a Claude
subagent's are.

**Warning: this API is in early access.** Claude Mods (function hooks) load only with
`CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1`, and Anthropic says the API may change between releases
without notice. Everything below was checked on 2026-09-19. Re-read the type declarations before
building on any of it: event names, the 10 s hook budget and the 10 min cap on `$.process.run` may
all have changed.

**Where the research is**, in [research/claude-mods/](../../research/claude-mods/):
- [verification.md](../../research/claude-mods/verification.md): the API checked by hand against
  Anthropic's source, three integration paths and a verdict. Start here.
- `postmortem.md`: why the explorer wrongly concluded that Mods do not exist.
- The explorer's own notes: its general notes on subagents, plugins and MCP are usable; its
  "Mods do not exist" finding is wrong.

**References:**
- [anthropics/claude-code/mods](https://github.com/anthropics/claude-code/tree/main/mods): the
  official source, including the three built-in mods. The whole API is declared in
  `mods/types/claude-code.d.ts`. Look for `turn.step`, `agent.register`, `agent.spawn`,
  `tool.register`, `tool.call`, `$.process.run`, `$.http.fetch` and `HookBudget`.
- [FazalAAli/pi-agent-for-claude](https://github.com/FazalAAli/pi-agent-for-claude) (MIT): a
  working example. It runs the [pi](https://pi.dev) CLI as a native subagent type. A `turn.step`
  hook replaces the subagent's model requests with a detached `pi --mode json` run. pi's text and
  thinking are streamed back, and each pi tool call appears as a `▸ tool: args` line. A no-op
  `pi_progress` tool chains the steps so the 10 s hook budget is never exceeded.
  `hooks/register.ts` is the file to adapt.
- Secondary write-ups: [Wavect](https://wavect.io/blog/claude-mods-function-hooks/) and
  [aitmpl](https://www.aitmpl.com/mods/).
- Our side: [Serving the agents](../operations/serving.md), which returns 202 and a task id, then polls, and
  [Delegation](../agents/delegation.md).

**Paths, simplest first:**
1. **No mod.** A skill or `.claude/agents/*.md` that runs `python -m agent.<name>` with Bash
   `run_in_background`. This works today. It gives no live view, only the result.
2. **Copy pi-agent-for-claude.** Swap `pi --mode json` for our agent, emitting one JSONL event
   per tool start. The trace writer already has these events. This gets the live view, but only
   as text lines: no real tool rows, no results, and it bypasses Claude Code's permission
   prompts.
3. **Swap the model, not the agent.** A `turn.step` hook answers a registered agent type's model
   requests from the pool. Claude Code runs the tools, so they appear as real tool rows, which is
   exactly the goal. The cost: the router needs an HTTP chat endpoint, and the agent doing the
   work is Claude Code's harness rather than `agent/code`. That goes against "the agent is not
   bespoke" in CLAUDE.md, so it would have to be an experiment compared against the baseline.
4. **Tools only.** `tool.register` and `tool.call` submit to `agent/serve` and poll it. This is
   the simplest mod, but it has no live view.

**Blockers found:**
- pi-agent-for-claude is macOS/Linux only. It uses `sh`, `nohup`, `/tmp`, `ps` and `pkill`, and
  this machine runs Windows. Use WSL or port those calls.
- It needs Claude Code 2.1.275 or later.
- It relies on undocumented engine behaviour, so any Claude Code update can break it.
