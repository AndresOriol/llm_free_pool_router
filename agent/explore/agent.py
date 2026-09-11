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

from agent.runtime import run_tree
from agent.runtime.pool import CONTEXT_FLOOR, connect as connect_model
from agent.runtime.prompts import fill, shared_values
from agent.runtime.trace import tracer_from_env
from agent.runtime.web import check_pool, search

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
# Pages fetched per search. Upstream fetches one; one bad page is a dead end.
PAGES_PER_SEARCH = 2

# The system prompt of each agent, as the files it is joined from.
ORCHESTRATOR_PROMPTS = ("system.md", "workflow.md", "delegation.md")
RESEARCHER_PROMPTS = ("researcher.md",)

# Framework tools this agent is not offered: repository discovery it does not
# do (15.5.1). There is no `execute` to remove: the backend cannot run anything,
# so the framework never offers one.
EXCLUDED_TOOLS = ("ls", "glob", "grep")

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
    """(model, eligible member count, search pool). Refuses before the run.

    Two unrelated pools: models serve the conversation, Tavily accounts serve the
    searching. Search is checked first, because an explorer that cannot reach
    the web can never do this job.
    """
    from llm_router import TavilyPoolRouter

    search_pool = TavilyPoolRouter.from_env()
    check_pool(search_pool)
    model, members = connect_model(floor)
    return model, members, search_pool


# --- 4. Tools -------------------------------------------------------------------

def research_tools(pool, workdir: Path, research_dir: str,
                   described: dict) -> list:
    """`tavily_search`, `think_tool` and `research_status`, described by `tool_descriptions/`."""
    from langchain_core.tools import StructuredTool

    def tavily_search(query: str) -> str:
        return search(pool, query, max_results=PAGES_PER_SEARCH)

    def think_tool(reflection: str) -> str:
        return f"Reflection recorded: {reflection}"

    def research_status() -> str:
        notes = sorted((workdir / research_dir).rglob("*.md"))
        if not notes:
            return (f"/{research_dir}/ is empty -- nothing has been written yet. "
                    f"The files you write there are this run's only deliverable.")
        return f"Files in /{research_dir}/:\n\n" + "\n".join(
            f"/{note.relative_to(workdir).as_posix()} "
            f"({note.stat().st_size:,} bytes)" for note in notes)

    return [StructuredTool.from_function(func=func, name=func.__name__,
                                         description=described[func.__name__])
            for func in (tavily_search, think_tool, research_status)]


class ToolSurface(AgentMiddleware):
    """Fit the framework's tools and prompt to this agent, on every model call.

    `create_deep_agent` injects its tool suite and its prompt sections per call,
    so this is where they can be edited: drop `EXCLUDED_TOOLS`, describe every
    tool from `tool_descriptions/`, and cut `PRUNED_SECTIONS` out of the system prompt.
    Tools are copied rather than changed, because the graph shares them.
    """

    def __init__(self, described: dict) -> None:
        super().__init__()
        self.described = described

    def _apply(self, request):
        tools = []
        for tool in request.tools:
            name = getattr(tool, "name", "")
            if name in EXCLUDED_TOOLS:
                continue
            if name in self.described:
                tool = tool.model_copy(
                    update={"description": self.described[name]})
            tools.append(tool)

        system = request.system_message
        if system is not None:
            text = system.text
            for section in PRUNED_SECTIONS:
                text = text.replace(section, "")
            system = SystemMessage(re.sub(r"\n{3,}", "\n\n", text).strip())
        return request.override(tools=tools, system_message=system)

    def wrap_model_call(self, request, handler: Callable):
        return handler(self._apply(request))

    async def awrap_model_call(self, request, handler: Callable[..., Awaitable]):
        return await handler(self._apply(request))


# --- 5. The agent ---------------------------------------------------------------

def subagents(tools: list, values: dict, described: dict) -> list:
    """The two agents the orchestrator can delegate to.

    Each gets this surface and its own search limit. `general-purpose` is not
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
                ToolSurface(described)]

    return [
        {"name": "research-agent",
         "description": ("Delegate research to the sub-agent researcher. Only "
                         "give this researcher one topic at a time."),
         "system_prompt": prompt(RESEARCHER_PROMPTS, values),
         "tools": tools, "middleware": middleware()},
        {**GENERAL_PURPOSE_SUBAGENT, "tools": tools, "middleware": middleware()},
    ]


def build_agent(workdir: Path, model, pool, *, floor: int = CONTEXT_FLOOR,
                members: int = 0, research_dir: str = RESEARCH_DIR):
    """The research orchestrator over a jailed workdir, the pool and the web."""
    from deepagents import create_deep_agent

    from agent.runtime.backend import JailedFilesystemBackend

    workdir = Path(workdir)
    research_dir = research_dir.strip("/") or RESEARCH_DIR
    # Created up front: an agent left to `mkdir` it sometimes writes to `/`.
    (workdir / research_dir).mkdir(parents=True, exist_ok=True)

    values = template_values(floor, members, research_dir)
    described = descriptions(values)
    tools = research_tools(pool, workdir, research_dir, described)

    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=prompt(ORCHESTRATOR_PROMPTS, values),
        # Where read_file, write_file and edit_file read and write: the
        # workdir, jailed. Files only -- it has no `execute`, so none is offered.
        # Sub-agents share it.
        backend=JailedFilesystemBackend(root_dir=str(workdir)),
        # Caller middleware runs after the framework's, so it sees the tools and prompt sections those inject.
        middleware=[ToolSurface(described)],
        subagents=subagents(tools, values, described),
    )


# --- 6. A run -------------------------------------------------------------------

def run(model, task: str, workdir: Path, pool, config=None,
        floor: int = CONTEXT_FLOOR, members: int = 0,
        research_dir: str = RESEARCH_DIR,
        trace_path: Optional[Path] = None) -> tuple:
    """One exploration. Returns (final_state, run record written or None)."""
    config = dict(config or {})
    config.setdefault("recursion_limit", RECURSION_LIMIT)
    workdir = Path(workdir)

    jsonl = tracer_from_env()
    if jsonl is not None:
        config["callbacks"] = list(config.get("callbacks") or []) + [jsonl]

    agent = build_agent(workdir, model, pool, floor=floor, members=members,
                        research_dir=research_dir)
    with collect_runs() as collected:
        final = agent.invoke({"messages": [HumanMessage(task)]}, config)

    written = run_tree.record(collected.traced_runs, trace_path, {
        "workdir": str(workdir),
        "harness": "explore",
        "research_dir": research_dir,
        "context_floor": floor,
        "eligible_providers": members,
        "search_accounts": len(getattr(pool, "accounts", []) or []),
    })
    return final, written
