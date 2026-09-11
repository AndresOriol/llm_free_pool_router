"""The coding agent, assembled: model, prompt, backend, and the agent they make.

This file is the whole harness. Read top to bottom:

1. **Settings** -- the step budget, the programs `execute` runs, and the two
   prompt sections a run can switch off.
2. **Text** -- every word the model reads is a Markdown file: `prompts/` beside
   this one for the job, `agent/runtime/prompts/` for what all three agents are
   told about where they run. Each is a template, and `{name}` is filled from
   `template_values()`.
3. **Model** -- the pool, held to a context floor (`connect`).
4. **The project** -- what the agent knows before its first tool call.
5. **The agent** -- `build_agent()`: `create_deep_agent` over a jailed shell.
6. **A run** -- `run()`, and the wrap-up that lands the work of a run that
   spends its whole budget.

What the agent *does* is in the text, not here. Change a behaviour by editing a
Markdown file; the reasons for each setting are in
[6. The coding agent](../../docs/06-agent.md).

## How context reaches the model

Every call carries:

- the system prompt: `prompts/system.md`, with the project (branch, status,
  tree) and the agents it may run filled in once, when the run starts;
- the framework's own prompt sections and tool schemas -- the file tools,
  `execute`, `write_todos` and `task` -- as deepagents writes them. Nothing is
  taken away or re-described;
- the conversation so far. The framework summarizes it when it grows too long.

`task` starts a `general-purpose` sub-agent with the framework's own short
prompt, the same tools and backend, and a conversation of its own; only its
reply comes back. What outlives every conversation is the repository: the files
on disk and the commits.

## Ported from deepagents-code

`build_agent` is this project's `create_cli_agent`
(`libs/code/deepagents_code/agent.py`, MIT), in the same order:

| dcode | here |
| --- | --- |
| generated system prompt, interactive or headless | `prompts/system.md`, always headless |
| `LocalContextMiddleware` (git, tree) | `project_section`, once into the prompt |
| `ShellAllowListMiddleware` when non-interactive | the same, always (agent/runtime/shell.py) |
| `LocalShellBackend`, `virtual_mode=False` | `RestrictedShellBackend`, jailed |
| `general-purpose` subagent so `task` exists | same |
| `AskUserMiddleware` | never installed -- nobody is watching |
| HITL approval, cost tracking, MCP, skills, rubric | not carried over |
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Optional, Sequence

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tracers.context import collect_runs
from langgraph.errors import GraphRecursionError

from agent import delegation
from agent.runtime import run_tree
from agent.runtime.pool import CONTEXT_FLOOR, connect  # noqa: F401 - section 3
from agent.runtime.prompts import fill, shared_values
from agent.runtime.shell import ShellAllowListMiddleware
from agent.runtime.trace import tracer_from_env

logger = logging.getLogger("harness.code")

HERE = Path(__file__).parent

# --- 1. Settings --------------------------------------------------------------

# Supersteps, not agent turns, and a *budget*, not a loop guard: a recorded run
# reached 120 in 220 seconds of productive work -- reading a directory, writing
# five files, installing a toolchain and compiling it. Spending it all is an
# ordinary way for a run to end (docs/06-agent.md).
RECURSION_LIMIT = 400

# Held back from that budget, so a run that spends it is told so and still has
# the steps to say what is left and commit what it has (`prompts/wrap_up.md`).
# Without it the stop raised straight out of the graph, and a session that had
# written working code reported nothing and left it uncommitted.
WRAP_UP_RESERVE = 40

# What `execute` runs: the backend's default allowlist plus `git`. One list,
# read by the prompt that states it and the middleware that explains a refusal;
# the backend is what enforces it.
ALLOWED_PROGRAMS = ("python", "python3", "py", "pytest", "git")

# Two prompt sections, each on by default so it is a configuration an A/B
# can measure. `=0` leaves the file out: that is the other arm.
INVARIANT_GUARD_ENV = "AGENT_INVARIANT_GUARD"  # prompts/contradicted_requests.md
WRITE_ACCOUNT_ENV = "AGENT_WRITE_ACCOUNT"      # prompts/project_notes.md
_OFF = {"0", "", "off", "false", "no"}


# --- 2. Text --------------------------------------------------------------------

def prompt(name: str, values: dict) -> str:
    """One file from `prompts/`, filled."""
    return fill(HERE / "prompts" / name, values)


def optional(env: str, name: str) -> str:
    """A file from `prompts/`, or '' when `env` switches it off."""
    if os.environ.get(env, "1").strip().lower() in _OFF:
        logger.info(f"{env} is off: {name} is left out of the prompt.")
        return ""
    return prompt(name, {})


def template_values(floor: int = CONTEXT_FLOOR, members: int = 0,
                    programs: Sequence[str] = ALLOWED_PROGRAMS,
                    project: str = "", peers: str = "") -> dict:
    """What every `{placeholder}` in `prompts/system.md` is filled with."""
    return {
        # Shared with the other agents: where they run, not what they do.
        **shared_values(floor, members, programs),
        "invariant_guard_section": optional(INVARIANT_GUARD_ENV,
                                            "contradicted_requests.md"),
        "account_section": optional(WRITE_ACCOUNT_ENV, "project_notes.md"),
        "project_section": project,
        "delegation_section": peers,
    }


def system_prompt(values: dict) -> str:
    """`prompts/system.md`, filled. A section left out leaves no gap."""
    return re.sub(r"\n{3,}", "\n\n", prompt("system.md", values)).strip() + "\n"


# --- 3. Model -------------------------------------------------------------------
#
# The model is the pool: `connect(floor)` (agent/runtime/pool.py) returns a
# RouterChatModel that routes only to members holding `floor` input tokens, and
# how many there are, which the prompt states.


# --- 4. The project -------------------------------------------------------------
#
# dcode injects this on every call. Here it is built once into the prompt: the
# tree and the branch barely move within a run, and on a pool where a step costs
# a request against a daily quota, re-sending them buys nothing. Without it the
# first thing a run does is spend two or three calls discovering the project --
# the most expensive calls in it, because nothing has been compacted yet.

# Never worth a line: caches, virtualenvs, and vendored trees.
_SKIP = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
         ".venv", "venv", "node_modules", ".idea", ".vscode", "dist", "build",
         ".eggs", ".tox"}
# Past a couple of hundred entries a listing stops being orientation and starts
# being the context budget.
MAX_ENTRIES = 200
MAX_DEPTH = 3


def _git(workdir: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", "-C", str(workdir), *args],
                                capture_output=True, encoding="utf-8",
                                errors="replace", timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def tree(workdir: Path, max_entries: int = MAX_ENTRIES,
         max_depth: int = MAX_DEPTH) -> str:
    """A depth-limited listing of the project, as jail-relative paths."""
    workdir = Path(workdir)
    lines: list[str] = []
    truncated = False

    def walk(directory: Path, depth: int) -> None:
        nonlocal truncated
        if depth > max_depth or truncated:
            return
        try:
            entries = sorted(directory.iterdir(),
                             key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            return
        for entry in entries:
            if entry.name in _SKIP or entry.name.startswith("."):
                continue
            if len(lines) >= max_entries:
                truncated = True
                return
            rel = entry.relative_to(workdir).as_posix()
            lines.append(f"/{rel}/" if entry.is_dir() else f"/{rel}")
            if entry.is_dir():
                walk(entry, depth + 1)

    walk(workdir, 1)
    if truncated:
        lines.append(f"... (listing stopped at {max_entries} entries)")
    return "\n".join(lines)


def project_section(workdir: Path) -> str:
    """The `### Project` section, or '' when there is nothing to say."""
    workdir = Path(workdir)
    parts: list[str] = []

    branch = _git(workdir, "rev-parse", "--abbrev-ref", "HEAD")
    if branch:
        parts.append(f"Git branch: `{branch}`")
        status = _git(workdir, "status", "--porcelain")
        if status:
            parts.append(f"Uncommitted changes: {len(status.splitlines())} file(s)")
        else:
            parts.append("Working tree is clean.")

    listing = tree(workdir)
    if listing:
        parts.append("Files:\n```\n" + listing + "\n```")

    if not parts:
        return ""
    return "### Project\n\n" + "\n\n".join(parts)


# --- 5. The agent ---------------------------------------------------------------

def build_agent(workdir: Path, model, *, floor: int = CONTEXT_FLOOR,
                members: int = 0, allow_shell: bool = False,
                peers: Optional[Sequence[str]] = None,
                extra_middleware: Optional[Sequence] = None):
    """The coding agent over a jailed workdir.

    `peers` names the other agents this one may run. It costs a paragraph in the
    prompt and no tool: they are commands, and the agent already has `execute`
    ([16. Delegation](../../docs/16-delegation.md)).
    """
    from deepagents import create_deep_agent
    from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT

    from agent.runtime.backend import RestrictedShellBackend

    workdir = Path(workdir)
    # HARNESS_SHELL: the backend runs anything, so there is no list to state
    # and nothing for the middleware to mirror.
    programs = () if allow_shell else ALLOWED_PROGRAMS
    values = template_values(floor, members, programs,
                             project=project_section(workdir),
                             peers=delegation.prompt_section(peers or [], workdir))

    middleware = list(extra_middleware or [])
    if programs:
        # Refuses a command as a message the model can read and correct from.
        middleware.append(ShellAllowListMiddleware(programs))

    return create_deep_agent(
        model=model,
        system_prompt=system_prompt(values),
        # Where the file tools and `execute` work: the workdir, jailed.
        # Sub-agents share it.
        backend=RestrictedShellBackend(root_dir=str(workdir), allow_git=True,
                                       allow_shell=allow_shell),
        middleware=middleware,
        # dcode ships this one so that `task` exists at all: with no subagent
        # the SDK does not install SubAgentMiddleware.
        subagents=[GENERAL_PURPOSE_SUBAGENT],
    )


# --- 6. A run -------------------------------------------------------------------

def _drain(agent, state: dict, config: dict, limit: int) -> tuple[dict, bool]:
    """Run the graph to `limit` supersteps. Returns (state, ran_out).

    Streamed, not invoked: `invoke` raises at the limit and hands back nothing,
    while `stream_mode="values"` yields the whole state after every superstep,
    so the last one to arrive is where the run got to.
    """
    final = state
    try:
        for chunk in agent.stream(state, dict(config, recursion_limit=limit),
                                  stream_mode="values"):
            final = chunk
        return final, False
    except GraphRecursionError:
        return final, True


def _settle(messages: list) -> list:
    """Drop trailing tool calls that nothing answered.

    The limit can fire between the model node and the tool node, and every
    provider in the pool rejects a tool call with no answer. Without this the
    wrap-up turn would die on a 400 instead of committing anything.
    """
    while messages and _has_unanswered_call(messages):
        messages = messages[:-1]
    return messages


def _has_unanswered_call(messages: Sequence) -> bool:
    answered = {m.tool_call_id for m in messages
                if isinstance(m, ToolMessage) and getattr(m, "tool_call_id", None)}
    return any(call["id"] not in answered
               for m in messages if isinstance(m, AIMessage)
               for call in (m.tool_calls or []))


def run(model, task: str, workdir: Path, config=None,
        floor: int = CONTEXT_FLOOR, members: int = 0,
        allow_shell: bool = False, peers: Optional[Sequence[str]] = None,
        trace_path: Optional[Path] = None) -> tuple:
    """One session. Returns (final_state, run record written or None)."""
    config = dict(config or {})
    budget = config.pop("recursion_limit", RECURSION_LIMIT)
    workdir = Path(workdir)

    # The flat JSONL every metric is summed over (evals/metrics.py). Inherited,
    # so it also sees the provider calls under RouterChatModel, which is what
    # makes a failover bounce countable
    # ([7.3](../../docs/07-observability.md)).
    jsonl = tracer_from_env()
    if jsonl is not None:
        config["callbacks"] = list(config.get("callbacks") or []) + [jsonl]

    agent = build_agent(workdir, model, floor=floor, members=members,
                        allow_shell=allow_shell, peers=peers)

    # Two phases, so that spending the budget ends the run instead of killing
    # it: everything but the reserve, then -- only if that runs out -- the
    # reserve, with one instruction: land the work.
    reserve = min(WRAP_UP_RESERVE, budget // 4)
    with collect_runs() as collected:
        final, ran_out = _drain(agent, {"messages": [HumanMessage(task)]},
                                config, budget - reserve)
        if ran_out:
            logger.warning("Step budget spent after %d supersteps; %d reserved "
                           "for wrap-up.", budget - reserve, reserve)
            messages = _settle(list(final.get("messages") or []))
            messages.append(HumanMessage(prompt("wrap_up.md", {
                "used": budget - reserve, "limit": budget, "left": reserve})))
            final, ran_out = _drain(agent, {**final, "messages": messages},
                                    config, reserve)
            if ran_out:
                logger.warning("Wrap-up did not finish inside its %d steps.",
                               reserve)
    final = dict(final or {})
    final["step_budget_spent"] = ran_out

    written = run_tree.record(collected.traced_runs, trace_path, {
        "workdir": str(workdir),
        "harness": "code",
        "context_floor": floor,
        "eligible_providers": members,
        # Delegation spends quota outside this conversation, so the record says
        # which agents this one was allowed to run
        # ([16](../../docs/16-delegation.md)).
        "peers": list(peers or []),
    })
    return final, written
