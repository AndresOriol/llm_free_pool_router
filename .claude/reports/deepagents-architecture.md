# `deepagents` architecture and context management

Investigated `deepagents==0.6.12` (installed at
`...\pythoncore-3.14-64\Lib\site-packages\deepagents`), specifically how it's
wired into this repo's [agent/coding_agent.py](../../agent/coding_agent.py).
Everything below is read directly from the installed source, not from docs.

## 1. How this project uses it

[agent/coding_agent.py](../../agent/coding_agent.py) calls:

```python
create_deep_agent(model=model, system_prompt=system_prompt, backend=backend)
```

- `model` is `RouterChatModel` — a custom `BaseChatModel` (not a string spec),
  so deepagents can never resolve a **harness profile** for it (see §7). No
  Anthropic/OpenAI prompt-tuning suffix, no provider-specific tool-description
  overrides ever apply — every model the router serves gets the exact same
  generic prompt.
- `backend` is `RestrictedShellBackend(FilesystemBackend + SandboxBackendProtocol)`
  — real disk under `workdir`, jailed via `virtual_mode=True`, with `execute`
  restricted to `python`/`pytest` run with `shell=False`.
- `system_prompt` is just the raw text of `CLAUDE.md`/`AGENTS.md` from the
  workdir — this project does **not** use deepagents' built-in `memory=`
  parameter (which would go through `MemoryMiddleware` and get an Anthropic
  cache-control breakpoint automatically). It's loaded once at `build_agent()`
  time and never refreshed mid-run.
- No `skills=`, no `subagents=` (so the default `general-purpose` subagent is
  auto-added), no `permissions=`, no custom `middleware=`, no
  `state_schema=`. Everything downstream of `create_deep_agent` is 100%
  library defaults.

This means essentially all the "refine context management" leverage
described below is currently untouched — every knob in §4 defaults to
whatever `compute_summarization_defaults` picks for a model with no `.profile`
(`RouterChatModel` has none), i.e. the **conservative fallback**: trigger at
170,000 tokens, keep last 6 messages, truncate large tool args once 20
messages accumulate. That threshold is almost certainly wrong for the
router's actual pool — some Groq free-tier models cap out far below 170k
input tokens, so a run can hit a hard context overflow on a small model
before `SummarizationMiddleware` ever decides to compact.

## 2. Entry point: `create_deep_agent` (`graph.py`)

`create_deep_agent(model, tools=None, *, system_prompt=None, middleware=(), subagents=None, skills=None, memory=None, permissions=None, backend=None, interrupt_on=None, response_format=None, state_schema=None, ...)`

It assembles a fixed **middleware stack** and hands it to `langchain.agents.create_agent`
(a LangGraph `StateGraph` compiler). The stack order (main agent), per the
docstring and code (`graph.py:773-841`):

```
TodoListMiddleware
SkillsMiddleware              (only if skills= given)
FilesystemMiddleware
SubAgentMiddleware             (only if any sync subagents exist)
SummarizationMiddleware        (create_summarization_middleware(model, backend))
PatchToolCallsMiddleware
AsyncSubAgentMiddleware        (only if async subagents given)
--- caller's middleware=[...] inserted here ---
HarnessProfile.extra_middleware (if a profile matched the model)
_ToolExclusionMiddleware       (if profile excludes tools)
AnthropicPromptCachingMiddleware   (unconditional, no-op for non-Anthropic)
BedrockPromptCachingMiddleware     (if langchain-aws installed, no-op otherwise)
MemoryMiddleware                (only if memory= given)
HumanInTheLoopMiddleware        (only if interrupt_on/permissions produce interrupts)
```

`FilesystemMiddleware` and `SubAgentMiddleware` are **required scaffolding**
(`_REQUIRED_MIDDLEWARE` at `graph.py:230`) — `excluded_middleware` on any
harness profile cannot remove them; attempting to raises `ValueError`.

Default built-in tools: `write_todos`, `ls`/`read_file`/`write_file`/`edit_file`/`glob`/`grep`,
`execute` (only functional if `backend` implements `SandboxBackendProtocol`),
and `task` (backed by `SubAgentMiddleware`, only present if a synchronous
subagent — inline or auto-added general-purpose — exists).

### System prompt assembly

Order is always `USER -> (BASE or CUSTOM) -> SUFFIX`, joined by blank lines
(`graph.py:114-144`, `857-863`):

- `USER` = the `system_prompt=` argument you pass in (this project's
  CLAUDE.md/AGENTS.md text). Always first, so caller instructions win.
- `BASE` = `BASE_AGENT_PROMPT` (the SDK's built-in ~110-line prompt, quoted
  in full below), unless a matched `HarnessProfile.base_system_prompt`
  replaces it wholesale (`CUSTOM`).
- `SUFFIX` = `HarnessProfile.system_prompt_suffix`, appended last so
  model-tuning guidance sits closest to the conversation history (LLMs
  attend most to what's nearest the actual turn).

`BASE_AGENT_PROMPT` (verbatim, `graph.py:71-113`):

```
You are a deep agent, an AI assistant that helps users accomplish tasks using tools. You respond with text and tool calls. The user can see your responses and tool outputs in real time.

## Core Behavior
- Be concise and direct. Don't over-explain unless asked.
- NEVER add unnecessary preamble ("Sure!", "Great question!", "I'll now...").
- Don't say "I'll now do X" — just do it.
- If the request is underspecified, ask only the minimum followup needed to take the next useful action.
- If asked how to approach something, explain first, then act.

## Professional Objectivity
- Prioritize accuracy over validating the user's beliefs
- Disagree respectfully when the user is incorrect
- Avoid unnecessary superlatives, praise, or emotional validation

## Doing Tasks
When the user asks you to do something:
1. Understand first — read relevant files, check existing patterns. Quick but thorough.
2. Act — implement the solution. Work quickly but accurately.
3. Verify — check your work against what was asked, not against your own output. Your first attempt is rarely correct — iterate.

Keep working until the task is fully complete. Don't stop partway and explain what you would do — just do it. Only yield back to the user when the task is done or you're genuinely blocked.

When things go wrong:
- If something fails repeatedly, stop and analyze why — don't keep retrying the same approach.
- If you're blocked, tell the user what's wrong and ask for guidance.

## Clarifying Requests
- Do not ask for details the user already supplied.
- Use reasonable defaults when the request clearly implies them.
- ...(alerting/monitoring-specific guidance)...

## Progress Updates
For longer tasks, provide brief progress updates at reasonable intervals — a concise sentence recapping what you've done and what's next.
```

This is *the* agent-defining prompt: task loop discipline (understand → act →
verify), terseness, and "don't stop until genuinely blocked." Because this
project passes its own `system_prompt` (CLAUDE.md contents) which sits
*before* this, CLAUDE.md instructions take precedence on conflict, but this
base prompt is always present underneath.

## 3. Task/planning tool: `TodoListMiddleware`

Not part of `deepagents` itself — it's imported from `langchain.agents.middleware`.
Gives the agent a `write_todos` tool so it can externalize its plan into a
todo list held in graph state rather than re-deriving it from scratch each
turn. This is itself a context-management technique: the plan is data, not
something the model has to reconstruct by re-reading the whole transcript.

## 4. Context management — the core of the ask

Three distinct mechanisms, layered, all backend-writing to keep the *removed*
content recoverable rather than truly lost.

### 4a. `SummarizationMiddleware` (`middleware/summarization.py`)

Wraps `langchain.agents.middleware.summarization.SummarizationMiddleware`
(the upstream LangChain implementation) and adds: backend-offload of evicted
history to markdown, inline-media (image/audio) extraction to files so
base64 blobs don't bloat the summary prompt, and a `truncate_args_settings`
pre-pass.

**Trigger** (`compute_summarization_defaults`, lines 261-298):

```python
if model.profile has max_input_tokens:
    trigger = ("fraction", 0.85)   # summarize at 85% of the model's context window
    keep    = ("fraction", 0.10)   # keep the most recent 10% of context untouched
else:
    trigger = ("tokens", 170_000)  # fixed fallback
    keep    = ("messages", 6)
```

A `ContextSize` is a `(kind, value)` tuple where `kind` is `"tokens"`,
`"messages"`, or `"fraction"`. `trigger` can also be a list (OR-combined) or a
`TriggerClause` dict (AND-combined, e.g. `{"tokens": 100000, "messages": 50}`
fires only once *both* hold).

**Algorithm** (`wrap_model_call`, lines 1377-1509), run on every model call
via `wrap_model_call` (not `before_model` — this is deliberate; it modifies
the *request* sent to the model, not graph state, until it commits):

1. Reconstruct "effective messages" from any prior summarization event
   (`summary_message + messages[cutoff_index:]`) — summarization events are
   stored in private state (`_summarization_event`), not by mutating the
   message list directly, until the model call actually succeeds.
2. Count tokens once (`count_tokens_approximately` by default, or a custom
   `token_counter`), shared between the truncate-check and summarize-check to
   avoid re-running tool-schema-cost token counting twice.
3. **Truncate-args pre-pass** (`_truncate_args`): if `truncate_args_settings.trigger`
   is set and hit, shorten `write_file`/`edit_file` tool_call `args` values
   older than the `keep` window to `value[:20] + "...(argument truncated)"`
   — a cheap win before paying for a real LLM summarization call. Disabled by
   default (`truncate_args_settings=None` in this project's usage).
4. **Should-summarize check**: if under threshold, just call the model with
   the (possibly arg-truncated) messages. If the underlying call still raises
   `ContextOverflowError` (provider says "too many tokens" despite the
   heuristic), *fall through* to step 5 anyway — heuristic token counting is
   approximate, so this is the safety net.
5. **Summarize**: partition into `messages_to_summarize` / `preserved_messages`
   at `cutoff_index` (respecting the `keep` window — never split a
   tool-call/tool-result pair). Upload any inline base64 media to
   `{artifacts_root}/conversation_history/media/{sha256[:16]}.{ext}` and
   rewrite those blocks to path references. Append the evicted messages
   (XML-formatted) to `/conversation_history/{thread_id}.md` on the backend
   — one growing file per thread, timestamped sections, never truncated.
   Then call the summarization model (default prompt below) on
   `messages_to_summarize` (itself capped to `trim_tokens_to_summarize`,
   default 4000 tokens, so the summarization call itself can't blow its own
   budget). Build a new `HumanMessage` (`additional_kwargs={"lc_source": "summarization"}`)
   pointing back at the saved file, and continue the turn with
   `[summary_message, *preserved_messages]`.
6. On an overflow-triggered fallback specifically, also runs
   `_clip_overflow_tail` (`_overflow_clip.py`) to shrink the trailing batch of
   consecutive `ToolMessage`s in the preserved tail: `read_file` results get
   head-sliced to ~4k chars with a pointer back to the original path (no new
   write needed — the file's already on the backend); any other large tool
   result gets fully offloaded to `/large_tool_results/{tool_call_id}` and
   replaced with `TOO_LARGE_TOOL_MSG`, a stub telling the agent to
   `read_file(file_path=..., offset=N, limit=K)` if it needs it back.

Default summary prompt = LangChain's `DEFAULT_SUMMARY_PROMPT` plus a spliced-in
addendum (`_MEDIA_REFERENCE_SUMMARY_PROMPT`) instructing the summarizer to
preserve `<image url="..."/>`-style reference tags rather than inventing
visual detail it can't see.

**Chained summarization**: previous summary `HumanMessage`s are filtered out
before re-offloading (`_filter_summary_messages`), so a second summarization
event doesn't re-save what the first one already persisted — the markdown
history file only ever grows by the *newly* evicted slice.

### 4b. Proactive per-tool-call eviction (`FilesystemMiddleware` + `_message_eviction.py`)

Independent of the token-threshold summarization above: any individual tool
result that's simply *large* (a huge `grep`/`execute` output, e.g.) gets
offloaded the moment it's produced, before it ever accumulates toward the
summarization trigger. Shared helpers in `_message_eviction.py`
(`_offload_tool_message_content`, `_create_content_preview`) write the full
content to `{prefix}/{tool_call_id}` on the backend and replace the message
with `TOO_LARGE_TOOL_MSG` (head+tail line-numbered preview + a pointer to
re-read via `read_file(offset=, limit=)`).

### 4c. The `DeltaChannel` message reducer (`graph.py:65-68`, `_messages_reducer.py`)

`DeepAgentState.messages` uses a custom `DeltaChannel` (not LangGraph's
default `add_messages` list-append reducer), with `snapshot_frequency=50`.
This is purely a **checkpoint-size** optimization, not a token-budget one:
plain list-append reducers make every checkpoint write serialize the *entire*
growing message list (O(N) per write, O(N²) over a run). `DeltaChannel`
persists only the delta writes and periodically snapshots, so checkpoint
storage stays O(N) total. Dedup is by message `.id` (assigned once by
LangGraph's `ensure_message_ids`); `RemoveMessage`/`REMOVE_ALL_MESSAGES`
tombstone entries. Relevant if you add a `checkpointer=` — this project
currently passes none, so it doesn't matter yet, but it will the moment you
add persistence across process restarts.

### 4d. `SummarizationToolMiddleware` / `compact_conversation` (opt-in, not used here)

A separate tool (`create_summarization_tool_middleware`) lets the *agent
itself* decide to compact ("I've finished this subtask, older context is
irrelevant") rather than waiting for the token threshold. Its system-prompt
snippet:

```
## Compact conversation Tool `compact_conversation`
You have access to a `compact_conversation` tool. This tool refreshes your context window to reduce context bloat and costs.
You should use the tool when:
- The user asks to move on to a completely new task for which previous context is likely irrelevant.
- You have finished extracting or synthesizing a result and previous working context is no longer needed.
```

Not wired into `create_deep_agent`'s default stack — must be added explicitly
via `middleware=[...]`.

## 5. Filesystem / backend abstraction (`backends/*.py`)

`BackendProtocol` defines: `ls`, `read`/`write`/`edit`, `glob`, `grep`,
`upload_files`/`download_files` (+ async twins), and optionally `execute`
(gated behind `SandboxBackendProtocol`, which `create_deep_agent` checks to
decide whether to expose the `execute` tool at all).

Concrete backends:

| Backend | Storage | Notes |
|---|---|---|
| `StateBackend` (default) | in-memory, part of LangGraph state | ephemeral — dies with the run/thread; used automatically if `backend=None` |
| `FilesystemBackend` | real disk | `virtual_mode=True` jails paths under `root_dir` (used here) |
| `StoreBackend` | LangGraph `BaseStore` | persists across threads (long-term memory) |
| `CompositeBackend` | routes by path prefix | e.g. `/memories/**` → `StoreBackend`, everything else → `StateBackend` |
| `LocalShellBackend` | disk + unrestricted shell | implements `SandboxBackendProtocol` with no allowlist — this project deliberately subclasses `FilesystemBackend` instead (`RestrictedShellBackend`) rather than using this, precisely to avoid its blast radius |
| `SandboxBackend` | remote/cloud sandbox execution | for hosted sandboxes |
| `LangSmithBackend` | LangSmith-hosted artifact storage | |
| `context_hub.py` | — | routing/dispatch glue between backend types |

The filesystem tools (`read_file`, `write_file`, etc.) are themselves a
context-offload mechanism: instead of keeping large content in the message
history, the agent reads/writes named paths and only small
excerpts/pointers travel through the LLM context. `SummarizationMiddleware`,
`_message_eviction.py`, and `_clip_overflow_tail` all write through this same
abstraction — offloaded conversation history, evicted large tool results,
and the summarization media cache are just files at well-known paths
(`/conversation_history/`, `/large_tool_results/`, `/conversation_history/media/`).

This project's `RestrictedShellBackend` (`agent/restricted_backend.py`)
subclasses `FilesystemBackend` and additionally implements
`SandboxBackendProtocol.execute` with an allowlist (`python`/`pytest`,
`shell=False`, secret env vars stripped, 100KB output cap, 300s timeout) —
the intended customization point for adding execution capability without
adopting `LocalShellBackend`'s unrestricted shell.

## 6. Subagent isolation (`middleware/subagents.py`, `middleware/async_subagents.py`)

The `task` tool (backed by `SubAgentMiddleware`) launches a **separate
compiled agent graph per subagent spec**, each with its own middleware stack
built the same way as the main agent (`TodoListMiddleware`,
`FilesystemMiddleware`, its own `create_summarization_middleware(subagent_model, backend)`,
`PatchToolCallsMiddleware`, plus its own skills/harness-profile middleware —
see `graph.py:643-663`). Critically: **each subagent gets its own
`SummarizationMiddleware` instance and its own token budget** — a subagent's
conversation history is entirely separate from the parent's, so delegating a
large exploration task to a subagent is a legitimate way to keep that
exploration's tool-call noise out of the parent's context window entirely.
Only the subagent's *final message* is returned to the parent as a single
`ToolMessage` (stateless, single round-trip — no follow-up messages to a
subagent after it returns; TASK_TOOL_DESCRIPTION calls this out explicitly).

`shared backend` = the *same* backend instance is passed to every subagent
(`FilesystemMiddleware(backend=backend, ...)`), so subagents can still read
files the parent (or a sibling subagent) wrote, and vice versa — files are
the shared-state channel; messages are not.

`GENERAL_PURPOSE_SUBAGENT` is auto-added unless the caller supplies their own
`general-purpose`-named subagent or a harness profile disables it. Its
description and prompt are generic ("handles complex, multi-step tasks...").

`AsyncSubAgentMiddleware` is a different mechanism for background/remote
subagents (LangSmith-deployed graphs) — launch/check/update/cancel/list tools
instead of a blocking `task` call; independent of `interrupt_on` inheritance
and harness profiles.

## 7. Harness profiles (`profiles/`)

Two independent profile systems:

- **`ProviderProfile`** (`profiles/provider/`) — shapes *model construction*
  (`init_chat_model` kwargs). Only matters if you pass a `provider:model`
  string to `model=`. Irrelevant here since this project passes a pre-built
  `RouterChatModel` instance.
- **`HarnessProfile`** (`profiles/harness/`) — shapes *how the agent runs*
  once the model exists: `base_system_prompt` (replaces `BASE_AGENT_PROMPT`),
  `system_prompt_suffix` (appended last), `tool_description_overrides`,
  `excluded_tools`, `excluded_middleware` (by class or by `.name` string —
  cannot touch `FilesystemMiddleware`/`SubAgentMiddleware`), `extra_middleware`
  (appended to every stack), and `general_purpose_subagent` overrides.

Resolution (`_harness_profile_for_model`, `harness_profiles.py:1255-1325`):
looked up by exact `provider:model` spec string first, then provider prefix,
merging field-by-field if both exist (`_merge_profiles`). **For a pre-built
`BaseChatModel` instance with no `spec` string** (this project's case), it
falls back to introspecting `get_model_identifier(model)` /
`get_model_provider(model)` — which for a custom class like `RouterChatModel`
almost certainly yield nothing usable, so **no harness profile ever applies**
here, logged at DEBUG (`"No harness profile matched pre-built model..."`).

Built-in profile example, `_anthropic_sonnet_4_6.py`: registers
`"anthropic:claude-sonnet-4-6"` with a `system_prompt_suffix` containing three
tagged blocks — `<use_parallel_tool_calls>`, `<investigate_before_answering>`,
`<tool_result_reflection>` (quoted in full in that file) — Anthropic's
published Claude prompting guidance. Sonnet-4.6-specific tuning is
deliberately absent per the module's own docstring (reserved for a future
Sonnet-specific overlay if Anthropic publishes one).

`HarnessProfileConfig` is the YAML/JSON-serializable subset (no
`extra_middleware`, since that's runtime object state) for file-based
profile registration.

## 8. Other middleware, briefly

| Middleware | Purpose |
|---|---|
| `SkillsMiddleware` | Loads Anthropic Agent-Skills-format `SKILL.md` directories from backend paths (progressive disclosure — skill descriptions go in the prompt, full content loaded on demand), layered by source order (last wins on name collision) |
| `MemoryMiddleware` | Loads `AGENTS.md`-spec files (HTML comments stripped) from backend paths into the system prompt at startup; the intended library mechanism this project bypasses by hand-loading CLAUDE.md/AGENTS.md itself |
| `PatchToolCallsMiddleware` | Repairs/normalizes malformed tool-call structures before they reach the model API (defensive, provider-quirk handling) |
| `_ToolExclusionMiddleware` | Applied last among tool-injecting middleware; strips `HarnessProfile.excluded_tools` from the final visible tool set regardless of which layer added them |
| `HumanInTheLoopMiddleware` (from `langchain.agents.middleware`) | Pauses execution at configured tool names for approval; auto-installed whenever `interrupt_on=` or an `"interrupt"`-mode `FilesystemPermission` rule is present |
| `rubric.py` (`RubricMiddleware`) | Self-evaluated iteration loop: whenever the agent would otherwise finish (a response with no further tool calls), invokes a separate grader sub-agent against the transcript against a caller-declared rubric. `satisfied`/`failed` end the loop; `needs_revision` injects the grader's feedback as a `HumanMessage` and resumes, up to `max_iterations`. Grader sees at most the most recent 30 transcript messages (plus the original user prompt prepended if it fell outside that window), to bound grading cost. Not wired into `create_deep_agent`'s default stack — opt-in via `middleware=[...]` |
| `AnthropicPromptCachingMiddleware` / `BedrockPromptCachingMiddleware` | Unconditional in the stack; add `cache_control` breakpoints for Anthropic/Bedrock models, no-op for anything else (including this project's `RouterChatModel`, so prompt caching from these middlewares is currently inert here — the router's actual providers, if Anthropic-compatible, get no caching benefit from this layer) |
| `_state.py` | `private_state_field_names` — computes which state keys are middleware-private so they're excluded from what subagents inherit |
| `_fs_interrupt.py` | Translates `FilesystemPermission(mode="interrupt")` rules into `HumanInTheLoopMiddleware`'s `interrupt_on` config |
| `_tool_exclusion.py`, `_utils.py`, `_excluded_middleware.py`, `_models.py` (`resolve_model`, `get_model_identifier`/`get_model_provider`), `_tools.py` (`_apply_tool_description_overrides`) | Internal plumbing for the assembly logic in §2 |

## 9. Concrete ways to adapt context management here

Given §1 (nothing customized yet) and §4 (defaults are the generic
170k-token / keep-6-messages fallback because `RouterChatModel` has no
`.profile`), the levers actually worth pulling, in order of effort:

1. **Fix the summarization trigger for the pool's real limits.** Pass an
   explicit `middleware=[create_summarization_middleware(model, backend, trigger=("tokens", N), keep=("messages", M))]`
   in `build_agent()`, sized to the *smallest* free-tier model's context
   window the router might route to — otherwise a step that lands on a
   small-context Groq model can `ContextOverflowError` before the generic
   170k default ever fires. Since `RouterChatModel` fronts multiple models
   with different context sizes, a single static number is a compromise; a
   `TriggerClause` combining `messages` and `tokens` (AND-semantics) is safer
   than either alone.
2. **Give `RouterChatModel` a `.profile` (or per-provider harness profiles).**
   If `RouterChatModel` exposed `model.profile["max_input_tokens"]` based on
   whichever provider it's currently bound to, `compute_summarization_defaults`
   would switch to the fraction-based path automatically — but since routing
   happens *per-call* while the middleware is constructed once at agent-build
   time, a single static profile can't reflect the actively-selected
   provider; this would need to be the smallest-common-denominator profile
   across the pool, or the middleware would need per-call reconfiguration
   (not supported by the current `wrap_model_call` API without subclassing).
3. **Use `memory=` instead of hand-loading CLAUDE.md/AGENTS.md.** Switching
   `build_agent()` to pass `memory=["/CLAUDE.md", "/AGENTS.md"]` (with
   `FilesystemBackend` already jailed to `workdir`) would pick up
   `MemoryMiddleware`'s cache-control breakpoint placement — free for
   Anthropic-backed pool members — at the cost of losing the current "loaded
   once, first-match-wins between CLAUDE.md/AGENTS.md" semantics (`memory=`
   concatenates *all* sources instead).
4. **Delegate large exploration to subagents deliberately** (§6) — since
   this project never customized `subagents=`, everything currently runs in
   the single main-agent context. For a "run continuously for hours" agent,
   routing broad/exploratory sub-tasks through the auto-added
   `general-purpose` subagent (or a custom-scoped one) keeps their tool-call
   volume from ever entering the parent's summarization budget at all.
5. **Add a `HarnessProfile`** keyed to whatever spec string makes sense for
   this pool (e.g. register one under a synthetic key and have
   `RouterChatModel` expose that as `get_model_identifier`/`get_model_provider`)
   if you want pool-wide prompt tuning (e.g. tags like Anthropic's
   `<use_parallel_tool_calls>`) applied uniformly — right now no profile can
   ever match, so any such guidance has to live in the `system_prompt=`
   argument (i.e., in CLAUDE.md itself) instead.
6. **Enable `truncate_args_settings`** on the summarization middleware —
   currently `None` (disabled) — to cheaply shrink old `write_file`/`edit_file`
   argument payloads before a real summarization LLM call is ever needed;
   likely a good fit given this is a coding agent that writes large files.
