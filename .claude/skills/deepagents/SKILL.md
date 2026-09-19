---
name: deepagents
description: Design, build or change an agent in this repo using LangChain's deepagents library - which extension point to reach for, what the library already ships, and how to verify the API before writing code. Use whenever touching agent/code, agent/explore, agent/improve, agent/utils, anything calling create_deep_agent, or when adding a tool, prompt, skill, subagent, middleware or backend to an agent.
---

# Building agents with deepagents

The rule this skill exists to enforce: **the library is the harness; this repo
supplies only what the pool makes different.** Every line of agent scaffolding
here is a line that has to survive a `deepagents` upgrade, so the default answer
to "how do I change how the agent behaves" is *configuration or prose*, never a
new module.

Before you write anything, you owe three checks.

## 1. Check the version, always

`deepagents` moves fast and your training data is behind it. **The installed
package is ground truth. The docs site is not** — `docs.langchain.com` currently
claims a user middleware "matched by `.name` replaces built-ins"; in 0.6.12 that
raises `AssertionError: Please remove duplicate middleware instances.` Verified
by running it.

Start every task here:

```bash
python -c "import deepagents; print(deepagents.__version__, deepagents.__file__)"
```

Then read the actual signature and the actual assembly order rather than
recalling them:

```bash
SP=$(python -c "import deepagents,os;print(os.path.dirname(deepagents.__file__))")
sed -n '/^def create_deep_agent/,/^    r\?"""/p' "$SP/graph.py"
grep -n "Middleware(\|middleware.append\|middleware.extend" "$SP/graph.py"
cat "$SP/__init__.py"
```

If a claim in this file disagrees with the installed source, **the source wins
and you fix this file** in the same change.

## 2. Check whether the library already does it

Before adding code, search the package for the thing you are about to build:

```bash
grep -rn "<the concept>" "$SP" --include=*.py | head
```

As of 0.6.12 the library already ships all of this, and none of it should be
reimplemented here:

| You want | It already exists as |
| --- | --- |
| file tools (`ls`/`read_file`/`write_file`/`edit_file`/`glob`/`grep`) | `FilesystemMiddleware` — automatic |
| shell (`execute`) | any backend satisfying `SandboxBackendProtocol` |
| a todo list | `TodoListMiddleware` — automatic |
| subagent delegation (`task`) | `SubAgentMiddleware`, via `subagents=` |
| context compaction | `SummarizationMiddleware` — automatic |
| on-demand procedures | `SkillsMiddleware`, via `skills=["/skills/"]` |
| persistent `AGENTS.md` notes | `MemoryMiddleware`, via `memory=[...]` |
| path jailing / disk / store / routing | `FilesystemBackend`, `StoreBackend`, `CompositeBackend` |
| per-tool approval gates | `interrupt_on=` / `permissions=` |
| self-grading against criteria | `RubricMiddleware` |
| dropping a built-in tool | `HarnessProfile(excluded_tools=...)` |
| rewording a built-in tool | `HarnessProfile(tool_description_overrides=...)` |

## 3. Read how upstream does it before inventing a shape

<https://github.com/langchain-ai/deepagents/tree/main/examples> is the library
authors' own answer to "what is this for". Read the one nearest your task before
designing: it shows the idiom they intend, and this repo has independently
rebuilt two of them.

| Example | What it is | Ours |
| --- | --- | --- |
| `better-harness` | an outer agent edits an inner agent's *surfaces* — prompts, tool files, skills, middleware — and a change is kept only if the **train + holdout** pass count improves | [agent/improve](../../../agent/improve/) + [evals](../../../evals/) |
| `ralph_mode` | autonomous looping, **fresh context each iteration**, filesystem and git as the only memory | [docs/design/long-run-harness.md](../../../docs/design/long-run-harness.md) |
| `rubric_middleware` | a grader model revises output until criteria pass | `RubricMiddleware` |
| `deploy-coding-agent`, `content-builder-agent` | the plain shapes: a coding agent, memory + subagents | [agent/code](../../../agent/code/) |
| `async-subagent-server` | agents behind HTTP | [agent/serve](../../../agent/serve/) |

Two things worth stealing rather than re-deriving:

- **`better-harness` splits train from holdout** and gates on the combined
  count. Ours gates on a signature replay with no holdout, which is how a
  harness change overfits the handful of scenarios it was diagnosed from.
- **`better-harness` declares its editable surfaces** instead of naming a file
  in prose. Our issues carry a free-text `lever`, which nothing can check.

They are examples, not the API — the installed source still wins on any
question of what a parameter does.

## Pick the lightest extension point that works

Ordered cheapest-first. This is the **one ladder** in this repo: the
`behaviour-change` loop, J2's recommendations and the trace reviews all name
their fix as a rung on it. Do not skip a rung without a reason you can write
down, and when changing behaviour, go down one only after the rung above has
been measured with a probe experiment.

1. **Prose** — a `prompts/*.md` or `tool_descriptions/*.md` edit. Always
   present, on every call. This is the first thing to try and usually the
   last thing needed.
2. **A skill** — `skills/<name>/SKILL.md`, reached by `skills=`. A *procedure*
   most runs never need. Only the name and description are charged per call; the
   body is read with `read_file` if the model decides it applies. The
   description is the gate — write the trigger, not a summary.
3. **A harness profile** — `register_harness_profile(model_id, HarnessProfileConfig(...))`
   for which tools are offered, their descriptions, a prompt suffix, extra
   middleware. Declarative, public, upgrade-safe.
4. **Model selection** — which pool members serve the agent
   (`llm_router/config.yaml`, `CONTEXT_FLOOR` in `agent/utils/pool.py`). Not a
   `deepagents` extension point, but it is this repo's answer to a judgement
   failure, and it is cheaper than anything below it.
5. **A tool** — a plain function passed to `tools=`. Additive only: it can never
   remove a built-in. Reach for this when the agent needs a capability nothing
   else provides (a search API, a ledger read).
6. **Middleware** — `middleware=[...]`, appended after the base stack. For
   cross-cutting concerns (logging, refusal messages). Keep state in graph
   state, never on the instance, and remember it does not reach the `task`
   subagent unless you pass it there (below).
7. **A subagent** — `subagents=[SubAgent(...)]`. For work that deserves its own
   context window and returns one report. Costs a whole conversation. Never
   the fix for a failure of judgement.
8. **A backend** — subclass `FilesystemBackend` / implement `SandboxBackendProtocol`.
   Justified only when *where files live or what may run* is genuinely different
   here. This repo has one legitimate case: the jail.
9. **Monkeypatching a private name** — `_underscore` attributes of the package.
   This is not an extension point. See below.

## The verified facts

**`create_deep_agent` parameters** (0.6.12): `model`, `tools`, `system_prompt`,
`middleware`, `subagents`, `skills`, `memory`, `permissions`, `backend`,
`interrupt_on`, `response_format`, `state_schema`, `context_schema`,
`checkpointer`, `store`, `debug`, `name`, `cache`.

**Prompt assembly**, in order: your `system_prompt=` → the SDK's
`BASE_AGENT_PROMPT` (or a profile's `base_system_prompt`) → profile
`system_prompt_suffix` → middleware-contributed sections (filesystem, subagent,
skills catalog) → memory. Your text always goes *first*; nothing you pass
removes the SDK's own sections.

**Middleware order**: `TodoListMiddleware` → `SkillsMiddleware` (if `skills`) →
`FilesystemMiddleware` → `SubAgentMiddleware` (near-always: see `task` below) →
`SummarizationMiddleware` → `PatchToolCallsMiddleware` → **your `middleware=`** →
profile `extra_middleware` → tool exclusion → prompt caching → `MemoryMiddleware`
→ `HumanInTheLoopMiddleware`.

**`task` is on by default, and it carries its own middleware stack.** This
file used to say the opposite -- that with no `subagents=` there is no `task`.
In 0.6.12 `create_deep_agent` *adds* a `general-purpose` subagent unless the
harness profile disables it
(`general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)`) or you
pass a spec named `general-purpose` yourself. Verified by listing the built
agent's tools.

The consequence is the part that bites. deepagents builds that auto-added
subagent a *fresh* stack -- `TodoListMiddleware`, `FilesystemMiddleware` over
**your backend**, summarization, `PatchToolCallsMiddleware` -- and **your
`middleware=` is not in it**. Anything you enforce as middleware is enforced on
the main agent only, so a boundary expressed that way has a hole the width of
`task`: the subagent holds `write_file` over the same jail. `agent/improve`
proved it, wrote a file through `task` that the parent refuses, and now passes
the spec so the boundary is installed on both:

```python
subagents=[{**GENERAL_PURPOSE_SUBAGENT, "middleware": middleware}]
```

A caller-supplied spec's `middleware` *is* carried through
(`graph.py`: `subagent_middleware.extend(spec.get("middleware", []))`), and
supplying one named `general-purpose` suppresses the auto-added default. If you
write middleware to enforce anything, assert it end to end through `task` --
`tests/agent/test_improve_tools.py::TestReadOnly` is the worked example.

**Rewording a built-in tool has a supported route.** Verified:

```python
from deepagents import HarnessProfileConfig, register_harness_profile

register_harness_profile("anthropic:claude-sonnet-4-5", HarnessProfileConfig(
    tool_description_overrides={"read_file": "Read the whole file by default."},
))
```

This reaches the built-in filesystem tools through
`FilesystemMiddleware(custom_tool_descriptions=...)`.

**Keying it for this pool has one trap.** A profile is looked up by model spec
string; for a *pre-built* model instance there is no spec, so deepagents falls
back to the provider from `model._get_ls_params()["ls_provider"]` — which
LangChain derives from **the class name**, not from `_llm_type`. The pool passes
a `RouterChatModel` instance, so the key is `"routerchatmodel"`. Registering
under `"router"` (its `_llm_type`) silently matches nothing: deepagents logs a
warning and uses defaults. Get the key from the class, never from a guess:

```bash
python -c "from agent.utils.chat_model import RouterChatModel as R; print(R(router=None)._get_ls_params()['ls_provider'])"
```

`agent/utils/file_tools.py` does this, and a test pins the key to the live
class so renaming `RouterChatModel` fails CI instead of quietly dropping the
override.

**One profile for three agents is not a law — it is a missing field.** Because
the key is the provider alone, a profile registered for this pool applies to
`code`, `explore` and `improve` at once, which is why each of them reaches past
the profile for middleware instead. But the lookup is
`f"{provider}:{identifier}"` with a **fallback to the provider**, and the
identifier is `model_name` or `model` on the instance
(`deepagents/_models.py:get_model_identifier`). `RouterChatModel` declares
neither, so the identifier is `None` and only the bare key can ever match.

Give it an optional `model_name` set per agent and each gets its own profile
**layered on the shared one** — verified, and they merge rather than replace:

```
model_name=None       -> excluded=[]                    read_file_override=True
model_name='explore'  -> excluded=['glob','grep','ls']  read_file_override=True
model_name='code'     -> excluded=[]                    read_file_override=True
```

That is the difference between extension point 3 being unavailable here and
being the default answer. `excluded_tools`, `tool_description_overrides`,
`base_system_prompt`, `system_prompt_suffix`, `excluded_middleware` and
`general_purpose_subagent` all become per-agent and declarative.

Two limits to know before leaning on it: `FilesystemMiddleware` and
`SubAgentMiddleware` are in `_REQUIRED_MIDDLEWARE` and `excluded_middleware`
raises `ValueError` rather than dropping them, so their prompt sections cannot
be removed this way; and excluding a middleware takes its *tools* with it, so it
is not a way to keep a tool and drop its prompt section.

## Anti-patterns, with the repo's own examples

**Rebinding private names at import.** `agent/utils/file_tools.py` used to
wrap `FilesystemMiddleware._create_read_file_tool` and rewrite
`READ_FILE_TOOL_DESCRIPTION` by anchored string surgery, to change one default —
240 lines across three private seams. It is now ~40 lines: a harness profile for
the description, and one public field default for the limit. Read it as the
worked example. When a patch is genuinely the only route it must be one
function, in one module, with a test asserting the seam — and an issue filed
upstream, because a patch with no upstream request is a permanent fork.

**Reimplementing the tool suite.** `agent/utils/tools.py` hand-wrote `ls`,
`glob`, `grep`, `read_file`, `edit_file`, `write_file` and `execute` — all of
which `FilesystemMiddleware` ships, better. It was dead code left by the deleted
narrow-role harness, and it has been removed. `agent/improve/tools.py` is the
same shape and still live: do not grow it.

**Re-describing what the framework already says.** Prompt text explaining tools
the SDK has already documented in their schemas is paid for on every call and
can contradict the real behaviour after an upgrade. Say what is *different*
here.

**A new module for a behaviour change.** If the diff for "the agent should do X"
is a `.py` file rather than a `.md` file, stop and re-read the ladder above.

## Writing it

Model construction stays the pool's job — `connect(floor)` from
`agent/utils/pool.py` returns a `RouterChatModel`, and it is passed as
`model=`. Never hardcode a provider string in an agent.

Keep the shape the existing agents use, because it is the one that works:
`agent/<name>/agent.py` builds and runs, `prompts/*.md` is everything the model
reads on every call, `skills/*/SKILL.md` is everything it reads on demand,
`__main__.py` is the command. A new agent that needs a new Python module to
express its behaviour is a design that has not been reduced yet.

After any change touching the library surface, run the seam tests:

```bash
python -m pytest tests/agent -q
```

## When the library is genuinely missing something

Then, in this order: (1) confirm against the installed source that it is really
absent, (2) write the smallest adapter in `agent/utils/`, (3) document the
seam and the upgrade risk in its module docstring, (4) add a test that fails
when the upstream API moves, (5) open the upstream issue. A local workaround
without (5) is a fork nobody decided to maintain.
