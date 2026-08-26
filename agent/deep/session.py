"""One deepagents session over one workdir, on the pool.

The coding arm. It keeps a conversation and makes it fit by staying on the
pool's *widest* members and letting the SDK compact the history. The narrow-role
graph it replaced did the opposite -- it split work so every call fitted the
pool's *narrowest* member -- and it is retired: the constraint that produced it
was lifted once a session could route to members holding 128,000 input tokens
([6. The coding agent](../../docs/06-agent.md)).

`build_agent` is this project's `create_cli_agent`
(`libs/code/deepagents_code/agent.py`, MIT): the function that turns a generic
deep agent into a coding agent. What it configures, in the same order dcode
does:

| dcode | here |
| --- | --- |
| generated system prompt, interactive or headless | `prompt.build`, always headless |
| `LocalContextMiddleware` (git, tree) | `context.section`, once into the prompt |
| `ShellAllowListMiddleware` when non-interactive | `shell.ShellAllowListMiddleware`, always |
| `LocalShellBackend`, `virtual_mode=False` | `RestrictedShellBackend`, jailed |
| `general-purpose` subagent so `task` exists | same |
| `AskUserMiddleware` | never installed -- nobody is watching |
| HITL approval, cost tracking, MCP, skills, rubric | not carried over |

The three seams that are ours rather than dcode's:

1. **The model is the pool.** `resolve_model` returns a `BaseChatModel`
   unchanged and `RouterChatModel` is one, so the router drops in with no
   adapter ([4.5](../../docs/04-failover.md#45-the-failover-loop)).
2. **A hard context floor.** Groq's members hold 8,000 input tokens and 100,000
   *per day*; one full-context request would spend an account's entire daily
   budget. A conversation cannot be trimmed to fit them, so this refuses to
   route below the floor and waits for a wide member. Groq stays in the pool for
   work that fits it.
3. **The record is a run tree** fetched from LangSmith (agent/deep/trace.py).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Sequence

from langchain_core.messages import HumanMessage
from langchain_core.tracers.context import collect_runs

from agent.deep import context, prompt
from agent.deep import trace as run_trace
from agent.deep.shell import ShellAllowListMiddleware
from agent.runtime.trace import tracer_from_env

logger = logging.getLogger("harness.deep")

# The floor, in input tokens. Sized to admit both Gemini families (250,000) and
# Gemma (128,000) while excluding every Groq member (8,000). A property of the
# pool, not a guess: raising it past 128,000 drops Gemma and leaves only the
# request-scarce Gemini accounts (llm_router/config.yaml).
CONTEXT_FLOOR = 128_000

# Supersteps, not agent turns. A backstop against a loop that never settles --
# on a free pool the daily request quota binds long before this does.
RECURSION_LIMIT = 120

# Kept in step with RestrictedShellBackend's own allowlist. The backend is the
# boundary; this is what the model gets told (agent/deep/shell.py).
ALLOWED_PROGRAMS = ("python", "python3", "py", "pytest", "git")


def build_agent(workdir: Path, model, *, floor: int = CONTEXT_FLOOR,
                members: int = 0, allow_shell: bool = False,
                extra_middleware: Optional[Sequence] = None):
    """The compiled coding agent over a jailed backend."""
    from deepagents import create_deep_agent
    from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT

    from agent.runtime.backend import RestrictedShellBackend

    workdir = Path(workdir)
    backend = RestrictedShellBackend(root_dir=str(workdir), allow_git=True,
                                     allow_shell=allow_shell)

    project = context.section(workdir)
    system_prompt = prompt.build(floor, members=members,
                                 extra_sections=[project] if project else None)

    middleware = list(extra_middleware or [])
    # Only meaningful while the backend still has an allowlist to mirror. With
    # allow_shell the backend permits anything, and a middleware refusing what
    # the backend would run is a rule that exists in one place only -- worse
    # than no rule, because it reads like a boundary and is not one.
    if not allow_shell:
        middleware.append(ShellAllowListMiddleware(ALLOWED_PROGRAMS))

    return create_deep_agent(
        model=model,
        system_prompt=system_prompt,
        backend=backend,
        middleware=middleware,
        # dcode always ships this one so the `task` tool exists at all; without
        # a subagent the SDK does not install SubAgentMiddleware.
        subagents=[GENERAL_PURPOSE_SUBAGENT],
    )


def check_floor(router, floor: int = CONTEXT_FLOOR) -> int:
    """Fail before the run rather than during it. Returns the eligible count.

    With a hard floor and no member wide enough, every step would walk its retry
    budget and die on "all providers exhausted" -- an error that describes a
    rate limit, not a pool that could never have served this agent.
    """
    wide = [p for p in router.providers
            if p.max_input_tokens is None or p.max_input_tokens >= floor]
    if not wide:
        raise SystemExit(
            f"No provider in the pool holds {floor:,} input tokens, so this "
            f"agent cannot run on it. Widen the floor with DEEP_CONTEXT_FLOOR, "
            f"or add a wide-context member to the pool.")
    logger.info(f"{len(wide)} of {len(router.providers)} providers meet the "
                f"{floor:,}-token floor.")
    return len(wide)


def _trace_locator(runs) -> tuple[Optional[str], Optional[str]]:
    """What the fetch needs to name this trace: `(trace_id, project_id)`.

    Reading any run's `trace_id` does not work here, and neither does taking
    `runs[0]`. `RunCollectorCallbackHandler` persists a run only when it has no
    parent, so what arrives is not one tree but *several detached roots* -- a
    session with two model turns and one tool call yields six, one per
    LangGraph turn plus one for each `RouterChatModel`/provider pair -- and each
    of them carries its own id as its `trace_id`. Asking LangSmith for a leaf's
    id returns an empty trace, and the v2 endpoint reports that as 200 with no
    runs: a trace recorded as `null` with nothing to say why.

    The session's own root is the one that started first; everything else
    begins inside it. `start_time` says so directly, and the collector's own
    order does not, because it appends in *completion* order and so puts the
    innermost LLM call first and the root last.

    `project_id` is the same field LangSmith calls `session_id`. The collector
    leaves it unset, so this is normally None and agent/deep/trace.py resolves
    the project by name instead.
    """
    if not runs:
        return None, None
    root = min(runs, key=lambda run: run.start_time)
    trace_id = getattr(root, "trace_id", None) or root.id
    project_id = getattr(root, "session_id", None)
    return str(trace_id), str(project_id) if project_id else None


def run_session(model, task: str, workdir: Path, config=None,
                floor: int = CONTEXT_FLOOR, members: int = 0,
                allow_shell: bool = False,
                trace_path: Optional[Path] = None) -> tuple:
    """Run one session. Returns (final_state, trace_written)."""
    config = dict(config or {})
    config.setdefault("recursion_limit", RECURSION_LIMIT)
    workdir = Path(workdir)

    # Two records, because they answer different questions and one of them
    # expires. The run tree below is the readable account of the session; the
    # flat JSONL is what every automatic metric is summed over
    # (evals/metrics.py), and it must exist on disk even when LangSmith is
    # unreachable or switched off. Registered as an inheritable callback so it
    # also sees the provider calls underneath RouterChatModel, which is what
    # makes a failover bounce countable ([7.3](../../docs/07-observability.md)).
    #
    # Passing callbacks here does not displace `collect_runs` below: the
    # collector arrives through a context var, and langchain_core adds those on
    # top of whatever the config carries rather than instead of it.
    jsonl = tracer_from_env()
    if jsonl is not None:
        config["callbacks"] = list(config.get("callbacks") or []) + [jsonl]

    agent = build_agent(workdir, model, floor=floor, members=members,
                        allow_shell=allow_shell)

    # `collect_runs` learns the trace and project ids from the same callbacks
    # LangSmith's tracer uses, so the fetch afterwards knows what to ask for
    # without a network round trip during the run.
    with collect_runs() as collected:
        final = agent.invoke({"messages": [HumanMessage(task)]}, config)

    trace_id, project_id = _trace_locator(collected.traced_runs)
    # Resolve here rather than inside the fetch, so the record names the
    # project the request actually queried.
    project_id = run_trace.resolve_project_id(project_id)

    written = None
    if trace_path is not None:
        tree = run_trace.fetch_tree(trace_id, project_id) if trace_id else None
        written = run_trace.write(trace_path, tree, meta={
            "trace_id": trace_id,
            "project_id": project_id,
            "workdir": str(workdir),
            "harness": "deepagents",
            "context_floor": floor,
            "eligible_providers": members,
            "tracing_enabled": run_trace.tracing_enabled(),
        })
        if written:
            logger.info(f"Wrote the run record to {written}")

    return final, written
