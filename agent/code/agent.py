"""The coding agent, assembled: model, prompt, backend, and the agent they make.

This file is the whole harness. Read top to bottom:

1. **Settings** -- the step budget, the programs `execute` runs, and the two
   prompt sections a run can switch off.
2. **Text** -- every word the model reads is a Markdown file: `prompts/` beside
   this one for the job, `skills/` for a procedure it reads only when it needs
   it, `agent/utils/prompts/` for what all three agents are told about where
   they run. Each prompt is a template, and `{name}` is filled from
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
- one line per skill: its name and a sentence saying when it applies. The body
  is a file the model reads with `read_file` if the moment comes, so a long
  procedure costs a line per step instead of its full length;
- the conversation so far. The framework summarizes it when it grows too long.

`task` starts a `general-purpose` sub-agent with the framework's own short
prompt, the same tools and backend, and a conversation of its own; only its
reply comes back. What outlives every conversation is the repository: the files
on disk and the commits.

## Skills

`skills/<name>/SKILL.md` is a procedure the agent reads when it is about to do
that kind of work. deepagents' `SkillsMiddleware` puts each one's name and
description in the system prompt and nothing else; the body is reached with
`read_file`.

Three things follow from that, and they are the whole of `render_skills()`:

- **the description is the gate.** It is the only part always in front of the
  model, so it states the trigger rather than describing the skill. A body the
  agent never opens is prose that does nothing;
- **it is rendered, not committed.** Which peers can be reached is probed at
  start-up, so the roster *and* the description are filled per run, into a
  temporary directory that dies with the agent. No peers, no skill, no
  middleware -- which is what keeps `AGENT_PEERS=` a baseline arm;
- **it is mounted.** The agent is jailed to a workspace that is usually not this
  repository, so a path outside it is a skill it cannot read. A
  `CompositeBackend` route puts the rendered directory at `/skills/`; `execute`
  is not path-routed, so what the agent may run is untouched.

The split with `prompts/` is what changes and when: a prompt section is on every
call and holds what is always true; a skill body is read on demand and holds a
procedure most runs never need ([6.6](../../docs/06-agent.md#66-skills)).

## Ported from deepagents-code

`build_agent` is this project's `create_cli_agent`
(`libs/code/deepagents_code/agent.py`, MIT), in the same order:

| dcode | here |
| --- | --- |
| generated system prompt, interactive or headless | `prompts/system.md`, always headless |
| `LocalContextMiddleware` (git, tree) | `project_section`, once into the prompt |
| `ShellAllowListMiddleware` when non-interactive | the same, always (agent/utils/shell.py) |
| `LocalShellBackend`, `virtual_mode=False` | `RestrictedShellBackend`, jailed |
| `general-purpose` subagent so `task` exists | same |
| `SkillsMiddleware` over `.claude/skills` | the same, over `skills/` beside this file |
| `AskUserMiddleware` | never installed -- nobody is watching |
| HITL approval, cost tracking, MCP, rubric | not carried over |
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Sequence

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tracers.context import collect_runs
from langgraph.errors import GraphRecursionError

from agent import delegation
from agent.utils import run_tree
from agent.utils.pool import CONTEXT_FLOOR, connect  # noqa: F401 - section 3
from agent.utils.prompts import fill, shared_values
from agent.utils.shell import ShellAllowListMiddleware
from agent.utils.trace import traced

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

# Skills: `<name>/SKILL.md` beside this file, mounted into the agent's
# filesystem at `SKILLS_ROOT` so `read_file` can reach one from whatever
# workspace the run is jailed to. deepagents' `SkillsMiddleware` puts the name
# and description in the prompt and nothing else; the model reads the body only
# if it decides the skill applies -- progressive disclosure, and no tool
# ([6.6](../../docs/06-agent.md#66-skills)).
SKILLS_DIR = HERE / "skills"
SKILLS_ROOT = "/skills/"

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


def render_skills(peers: Sequence[str], workdir) -> Optional[tempfile.TemporaryDirectory]:
    """This run's skills, rendered into a directory to mount at `SKILLS_ROOT`.

    A skill's body is fixed prose, which is why it is a committed file. What is
    not fixed is who this run can reach: `delegate`'s roster and its
    *description* are facts probed at startup, so the committed file is a
    template and the mounted copy is filled in.

    With no peers the skill is not written at all, and nothing is mounted. That
    is what keeps `AGENT_PEERS=` an arm with no delegation in it rather than one
    that reads how to delegate and then fails
    ([16.2.2](../../docs/16-delegation.md#1622-the-roster-and-the-how-to-are-a-skill)).

    The caller keeps the returned object: the directory is deleted when it is.
    """
    if not peers:
        return None
    rendered = tempfile.TemporaryDirectory(prefix="agent-skills-")
    values = {"delegate": delegation.skill_values(peers, workdir)}
    for name, filling in values.items():
        target = Path(rendered.name) / name
        target.mkdir()
        text = fill(SKILLS_DIR / name / "SKILL.md", filling)
        (target / "SKILL.md").write_text(text + "\n", encoding="utf-8")
    return rendered


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
# The model is the pool: `connect(floor)` (agent/utils/pool.py) returns a
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
    """A depth-limited listing of the project, as jail-relative paths.

    The walk is the backend's own `glob`, not a second implementation of one.
    It already returns POSIX paths relative to the same root the agent's file
    tools see, so this is the listing the agent would get by asking -- and a
    hand-rolled walk that drifted from it would describe a project slightly
    different from the one it can open.

    What is left here is the part that is about the prompt rather than the
    filesystem: what is never worth a line, how deep is orientation rather than
    noise, and where a listing stops being either.

    Directory-only lines went with the hand-walk. Every directory holding
    anything still appears in its files' paths, and an empty one was never
    orientation.
    """
    from agent.utils.backend import JailedFilesystemBackend

    found = JailedFilesystemBackend(root_dir=str(workdir)).glob("**/*")
    lines: list[str] = []
    for entry in (found.matches or []):
        path = entry["path"] if isinstance(entry, dict) else str(entry)
        if isinstance(entry, dict) and entry.get("is_dir"):
            continue
        parts = path.strip("/").split("/")
        if len(parts) > max_depth:
            continue
        if any(part in _SKIP or part.startswith(".") for part in parts):
            continue
        if len(lines) >= max_entries:
            lines.append(f"... (listing stopped at {max_entries} entries)")
            break
        lines.append(path)
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
    from deepagents.backends.composite import CompositeBackend
    from deepagents.middleware.skills import SkillsMiddleware
    from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT

    from agent.utils.backend import JailedFilesystemBackend, RestrictedShellBackend

    workdir = Path(workdir)
    # HARNESS_SHELL: the backend runs anything, so there is no list to state
    # and nothing for the middleware to mirror.
    programs = () if allow_shell else ALLOWED_PROGRAMS
    values = template_values(floor, members, programs,
                             project=project_section(workdir),
                             peers=delegation.prompt_section(peers or []))

    middleware = list(extra_middleware or [])
    if programs:
        # Refuses a command as a message the model can read and correct from.
        middleware.append(ShellAllowListMiddleware(programs))

    # Where the file tools and `execute` work: the workdir, jailed. Sub-agents
    # share it.
    jail = RestrictedShellBackend(root_dir=str(workdir), allow_git=True,
                                  allow_shell=allow_shell)

    # A skill is only usable if `read_file` can reach the path the middleware
    # prints, and the agent's `/` is a workspace that is usually not this
    # repository. So the rendered directory is mounted as a second route.
    # `execute` is not path-routed -- CompositeBackend always runs it on the
    # default backend -- so the jail around what the agent *runs* is untouched.
    # With no skills there is no route and no middleware, and the agent is
    # byte-for-byte what it was before skills existed.
    skills = render_skills(peers or [], workdir)
    backend = jail if skills is None else CompositeBackend(
        default=jail,
        routes={SKILLS_ROOT: JailedFilesystemBackend(root_dir=skills.name)})
    if skills is not None:
        middleware.append(SkillsMiddleware(
            backend=backend, sources=[(SKILLS_ROOT, "Harness")],
            # The framework's default is ~1,500 characters explaining
            # progressive disclosure, and it tells the model that some sources
            # are shared across other agent tools on the machine, which is not
            # true here. It is charged on every call, so it is a prompt file
            # like every other (`prompts/skills.md`). Read rather than
            # `fill`ed: its three placeholders are the middleware's to fill.
            system_prompt=(HERE / "prompts" / "skills.md").read_text(
                encoding="utf-8")))

    built = create_deep_agent(
        model=model,
        system_prompt=system_prompt(values),
        backend=backend,
        middleware=middleware,
        # dcode ships this one so that `task` exists at all: with no subagent
        # the SDK does not install SubAgentMiddleware.
        subagents=[GENERAL_PURPOSE_SUBAGENT],
    )
    # The rendered skills must outlive this function and die with the agent.
    built._skills = skills
    return built


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
    config = traced(config)
    budget = config.pop("recursion_limit", RECURSION_LIMIT)
    workdir = Path(workdir)

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

    written = run_tree.record(
        collected.traced_runs, trace_path,
        # Delegation spends quota outside this conversation, so the record says
        # which agents this one was allowed to run
        # ([16](../../docs/16-delegation.md)).
        run_tree.about(workdir, "code", floor, members, peers=list(peers or [])))
    return final, written
