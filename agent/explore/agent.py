"""The web explorer, assembled: model, tools, prompts, and the agent they make.

This file is the whole harness. Read top to bottom:

1. **Settings** -- the budgets, the research directory, what is taken away.
2. **Text** -- every word the model reads is a Markdown file beside this one:
   `prompts/` for the job and the method, `tool_descriptions/` for one
   description per tool.
   Each is a template, and `{name}` is filled from `template_values()`.
3. **Model and search pool** -- `connect()`.
4. **Tools** -- the three this agent adds, and `ToolSurface`, which fits the
   framework's own tools and prompt to this agent.
5. **The agent** -- `build_agent()`: an orchestrator over two sub-agents.
6. **A run** -- `run()`.

What the agent *does* is in the text, not here. Change a behaviour by editing a
Markdown file; the reasons for each setting are in
[15. The web explorer](../../docs/15-explorer.md).

## How context reaches the model

Every orchestrator call carries:

- the system prompt: `prompts/system.md`, `workflow.md` and `delegation.md`,
  then the list of sub-agents the framework generates;
- one schema per tool, described by `tool_descriptions/<name>.md`;
- the conversation so far -- the request, its own tool calls, and each
  sub-agent's reply. The framework summarizes it when it grows too long.

A sub-agent starts from `prompts/researcher.md`, the brief the orchestrator
wrote it, and the same tools. The pages it reads stay in its conversation and
end with it; only its reply comes back, and that reply points at the note it
saved. What outlives every conversation is the files in the research directory,
and `research_status` is how an agent sees them.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path
from typing import Awaitable, Callable, Optional

from deepagents.graph import BASE_AGENT_PROMPT
from deepagents.middleware.filesystem import FILESYSTEM_SYSTEM_PROMPT
from deepagents.middleware.subagents import TASK_SYSTEM_PROMPT
from langchain.agents.middleware.todo import WRITE_TODOS_SYSTEM_PROMPT
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tracers.context import collect_runs

from agent.utils import run_tree
from agent.utils.pool import CONTEXT_FLOOR, connect as connect_model, keyed
from agent.utils.prompts import fill, shared_values
from agent.utils.trace import traced
from agent.explore.tools import research_tools, search_pool

logger = logging.getLogger("harness.explore")

HERE = Path(__file__).parent

# --- 1. Settings --------------------------------------------------------------

# Where the notes go, relative to the workdir, unless the run names another.
# Naming an existing directory continues that investigation (15.5.2).
RESEARCH_DIR = "research"

# Supersteps: a budget, not a loop guard, sized as the coding agent's
# (agent/code/agent.py).
RECURSION_LIMIT = 400

# Upstream's budgets. The prompts state them; the search limit is also
# enforced, once per sub-agent invocation.
MAX_CONCURRENT_RESEARCH_UNITS = 3
MAX_RESEARCHER_ITERATIONS = 3
MAX_SEARCHES_PER_SUBAGENT = 5

# The system prompt of each agent, as the files it is joined from.
ORCHESTRATOR_PROMPTS = ("system.md", "workflow.md", "delegation.md")
RESEARCHER_PROMPTS = ("researcher.md",)

# Framework tools this agent is not offered: repository discovery it does not
# do (15.5.1). There is no `execute` to remove: the backend cannot run anything,
# so the framework never offers one.
EXCLUDED_TOOLS = ("ls", "glob", "grep")

# This agent's harness profile, keyed `provider:identifier`. `pool.keyed` puts
# the identifier on the model; without it deepagents falls back to the shared
# `routerchatmodel` profile and this agent's surface is never applied.
NL3 = r"\n{3,}"
NL2 = "\n\n"

AGENT = "explore"
PROFILE_KEY = f"routerchatmodel:{AGENT}"

# Tools `tool_description_overrides` does not reach. deepagents wires that field
# to `FilesystemMiddleware`'s tools and to `SubAgentMiddleware`'s `task`, but
# `write_todos` comes from langchain's `TodoListMiddleware`, which takes a
# `tool_description` deepagents never passes on. Excluding that middleware and
# supplying a configured one does not work either: the profile's exclusion is
# applied to the caller's middleware as well, so both copies are stripped and
# the tool disappears entirely (verified against 0.6.12). So this one stays in
# `FrameworkSurface`, and upstream's text -- which closes by insisting the
# answer belongs in the final message -- would otherwise contradict this
# agent's whole contract.
PROFILE_BLIND = ("write_todos",)

# Framework prompt sections that describe those tools, or say the answer is the
# final message. Imported, so an upstream rewording fails a test (15.5.3).
PRUNED_SECTIONS = (BASE_AGENT_PROMPT, FILESYSTEM_SYSTEM_PROMPT,
                   TASK_SYSTEM_PROMPT, WRITE_TODOS_SYSTEM_PROMPT)


# --- 2. Text --------------------------------------------------------------------

def template_values(floor: int = CONTEXT_FLOOR, members: int = 0,
                    research_dir: str = RESEARCH_DIR) -> dict:
    """What every `{placeholder}` in `prompts/` and `tool_descriptions/` is filled with."""
    return {
        "research_dir": research_dir,
        "date": date.today().isoformat(),
        "max_concurrent_research_units": MAX_CONCURRENT_RESEARCH_UNITS,
        "max_researcher_iterations": MAX_RESEARCHER_ITERATIONS,
        "max_searches_per_subagent": MAX_SEARCHES_PER_SUBAGENT,
        # Shared with the other agents: where they run, not what they do.
        **shared_values(floor, members),
    }


def prompt(names, values: dict) -> str:
    """A system prompt: the named files from `prompts/`, in order."""
    return "\n\n".join(fill(HERE / "prompts" / name, values) for name in names)


def descriptions(values: dict) -> dict:
    """`{tool name: description}`, one per file in `tool_descriptions/`."""
    return {path.stem: fill(path, values)
            for path in sorted((HERE / "tool_descriptions").glob("*.md"))}


# --- 3. Model and search pool ---------------------------------------------------

def connect(floor: int = CONTEXT_FLOOR):
    """(model, eligible member count). Refuses before the run.

    Two unrelated pools: models serve the conversation, Tavily accounts serve
    the searching. The search pool is built and checked first, because an
    explorer that cannot reach the web can never do this job -- and finding
    that out on the first `tavily_search` means a session has already spent
    its start-up. The pool itself is owned by [tools.py](tools.py); this only
    forces it into existence early.
    """
    search_pool()
    return connect_model(floor)


def register_surface(values: dict) -> None:
    """Declare this agent's tool surface, as a harness profile.

    Dropping `EXCLUDED_TOOLS` and describing the framework's tools from
    `tool_descriptions/` used to be middleware rewriting `request.tools` on
    every model call. Both are `HarnessProfileConfig` fields, which is the
    library's own route and reaches sub-agents without being handed to each one
    ([the deepagents skill](../../.claude/skills/deepagents/SKILL.md)).

    Keyed per agent rather than per pool: a per-agent profile *merges* with the
    shared one, so `file_tools.py`'s `read_file` default survives underneath.
    Without `pool.keyed` on the model this resolves to nothing and the
    framework's own surface comes back -- `test_the_profile_key_matches_the_live_model`
    is what makes that a CI failure rather than a quiet one.

    `base_system_prompt` is deliberately *not* used to cut `BASE_AGENT_PROMPT`.
    deepagents applies that field to declarative sub-agents too, with their own
    `system_prompt` as the base, so setting it here would blank the
    researcher's prompt. The cutting stays in `FrameworkSurface`.
    """
    from deepagents import HarnessProfileConfig, register_harness_profile

    register_harness_profile(PROFILE_KEY, HarnessProfileConfig(
        excluded_tools=frozenset(EXCLUDED_TOOLS),
        tool_description_overrides=descriptions(values),
    ))


class FrameworkSurface(AgentMiddleware):
    """What the framework injects that the harness profile cannot reach.

    Two jobs, both narrow, and both here only because the profile stops short:

    - **the prompt sections.** `PRUNED_SECTIONS` are appended per call and no
      field suppresses them. `FilesystemMiddleware` and `SubAgentMiddleware` are
      in `_REQUIRED_MIDDLEWARE`, so `excluded_middleware` refuses to drop them,
      and excluding a middleware would take its tools with it anyway. The
      sections are imported rather than quoted, so an upstream reword fails a
      test instead of silently leaving the text in (15.5.3).
    - **`PROFILE_BLIND` descriptions.** One tool, `write_todos`.

    Everything else about the tool surface is `register_surface`.
    """

    def __init__(self, described: dict) -> None:
        super().__init__()
        self.described = {name: described[name] for name in PROFILE_BLIND
                          if name in described}

    def _apply(self, request):
        tools = [tool.model_copy(update={"description": self.described[tool.name]})
                 if getattr(tool, "name", "") in self.described else tool
                 for tool in request.tools]

        system = request.system_message
        if system is not None:
            text = system.text
            for section in PRUNED_SECTIONS:
                text = text.replace(section, "")
            system = SystemMessage(re.sub(NL3, NL2, text).strip())
        return request.override(tools=tools, system_message=system)

    def wrap_model_call(self, request, handler: Callable):
        return handler(self._apply(request))

    async def awrap_model_call(self, request, handler: Callable[..., Awaitable]):
        return await handler(self._apply(request))


# --- 5. The agent ---------------------------------------------------------------

def subagents(tools: list, values: dict, described: dict) -> list:
    """The two agents the orchestrator can delegate to.

    Each gets `FrameworkSurface` and its own search limit; the rest of the tool
    surface reaches them through the harness profile. `general-purpose` is not
    ours to remove: the framework adds it unless a harness profile keyed on the
    model says otherwise, and every agent here shares one model. So it is
    declared, to hold it to the same surface -- the framework's default would
    inherit `grep` and no search limit (15.5.3).
    """
    from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
    from langchain.agents.middleware import ToolCallLimitMiddleware

    def middleware():
        # "continue": once the searches are spent the researcher can still
        # save what it found.
        return [ToolCallLimitMiddleware(tool_name="tavily_search",
                                        run_limit=MAX_SEARCHES_PER_SUBAGENT,
                                        exit_behavior="continue"),
                FrameworkSurface(described)]

    return [
        {"name": "research-agent",
         "description": ("Delegate research to the sub-agent researcher. Only "
                         "give this researcher one topic at a time."),
         "system_prompt": prompt(RESEARCHER_PROMPTS, values),
         "tools": tools, "middleware": middleware()},
        {**GENERAL_PURPOSE_SUBAGENT, "tools": tools, "middleware": middleware()},
    ]


def build_agent(workdir: Path, model, *, floor: int = CONTEXT_FLOOR,
                members: int = 0, research_dir: str = RESEARCH_DIR):
    """The research orchestrator over a jailed workdir, the pool and the web."""
    from deepagents import create_deep_agent

    from agent.utils.backend import JailedFilesystemBackend

    workdir = Path(workdir)
    research_dir = research_dir.strip("/") or RESEARCH_DIR
    # Created up front: an agent left to `mkdir` it sometimes writes to `/`.
    (workdir / research_dir).mkdir(parents=True, exist_ok=True)

    values = template_values(floor, members, research_dir)
    described = descriptions(values)
    tools = research_tools(workdir, research_dir, described)

    # Which tools this agent is offered and how they are described: declared
    # once, applied by the framework to this agent and its sub-agents alike.
    register_surface(values)

    return create_deep_agent(
        # Keyed so the profile above is the one that resolves.
        model=keyed(model, AGENT),
        tools=tools,
        system_prompt=prompt(ORCHESTRATOR_PROMPTS, values),
        # Where read_file, write_file and edit_file read and write: the
        # workdir, jailed. Files only -- it has no `execute`, so none is offered.
        # Sub-agents share it.
        backend=JailedFilesystemBackend(root_dir=str(workdir)),
        # Caller middleware runs after the framework's, so it sees the prompt
        # sections those inject.
        middleware=[FrameworkSurface(described)],
        subagents=subagents(tools, values, described),
    )


# --- 6. A run -------------------------------------------------------------------

def run(model, task: str, workdir: Path, config=None,
        floor: int = CONTEXT_FLOOR, members: int = 0,
        research_dir: str = RESEARCH_DIR,
        trace_path: Optional[Path] = None) -> tuple:
    """One exploration. Returns (final_state, run record written or None)."""
    config = traced(config)
    config.setdefault("recursion_limit", RECURSION_LIMIT)
    workdir = Path(workdir)

    agent = build_agent(workdir, model, floor=floor, members=members,
                        research_dir=research_dir)
    with collect_runs() as collected:
        final = agent.invoke({"messages": [HumanMessage(task)]}, config)

    written = run_tree.record(
        collected.traced_runs, trace_path,
        run_tree.about(workdir, "explore", floor, members, research_dir=research_dir)
    )
    return final, written
