"""One exploration over one workdir: the web in, files out.

**This branch runs LangChain's deep-research agent, not our own prose.** The
explorer's own prompt lost to it on every axis a recorded run could measure --
13 searches, 0 sources opened, one file written at the end, every query phrased
as keywords ([15.7](../../docs/15-explorer.md#157-measured-against-a-reference-research-agent))
-- so the shape here is upstream's and the deviations are the ones this pool
forces ([deep_prompts.py](deep_prompts.py), [research_tools.py](research_tools.py)).

The three things that changed, and what each was for:

1. **An orchestrator over a researcher sub-agent.** The orchestrator plans,
   delegates, consolidates citations and writes the report; it never searches.
   The sub-agent's raw page dumps stay in the sub-agent's context, which is
   what makes fetching whole pages affordable at all.
2. **`tavily_search` returns the page, not a summary of it.** The old
   `web_search`/`read_url` pair made reading optional and the agent always
   declined. This tool has no such affordance.
3. **`think_tool` after every search.** A forced pause between retrieving and
   deciding to retrieve again. The failure it addresses is thirteen searches
   that never asked whether the twelfth had added anything.

And one that is not upstream's, added after reading the traces of the runs
above: **the tool surface is chosen rather than inherited**
([tools.py](tools.py)). `create_deep_agent` hands every agent a coding agent's
suite; this one is offered no `ls`, `glob`, `grep` or `execute`, and reads its
own directory from the system prompt instead ([notes.py](notes.py)). The project
tree went with them: what this agent should read of a repository is what the
brief names, because the agent that owns the repository is the one delegating
([15.5](../../docs/15-explorer.md#155-what-it-is-allowed-to-do)).

What did *not* change: the loop, the jail, the pool, the failover, and the fact
that the deliverable is a file on disk that outlives the run. The A2A handler
above it ([a2a.py](a2a.py)) is untouched -- it still collects `/research/*.md`
and reports them as artifacts, which is why the report is written there rather
than at the workdir root the way upstream does.

The grounded-Gemini search this replaced (`web_search`/`read_url`, a Gemini
model searching on the agent's behalf and returning its summary) is deleted
rather than kept as a fallback. It cost no third-party credits, which was the
argument for keeping it, and it is also the thing that produced a run of 13
searches and 0 opened sources -- a fallback nobody should fall back to is just
a second prompt to keep true. `git log` has it
([15.9](../../docs/15-explorer.md#159-what-the-grounded-gemini-search-was)).
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

from langchain_core.messages import HumanMessage
from langchain_core.tracers.context import collect_runs

from agent.code import trace as run_trace
from agent.code.session import CONTEXT_FLOOR, RECURSION_LIMIT, _trace_locator
from agent.explore import deep_prompts, notes, prompt, research_tools
from agent.explore import tools as tool_surface
from agent.explore.research_tools import NoSearchPool, check_pool  # noqa: F401
from agent.runtime.trace import tracer_from_env

logger = logging.getLogger("harness.explore")

# Where the notes go unless the task says otherwise. Stated in the prompt and
# here, because the launcher creates it: an agent that has to `mkdir` before its
# first write spends a step discovering that, and sometimes writes to `/` instead.
RESEARCH_DIR = "research"

# Upstream's numbers, kept. The defaults bias to one sub-agent anyway, and
# lowering a documented limit before observing it spend anything would be
# guessing at a budget rather than measuring one.
MAX_CONCURRENT_RESEARCH_UNITS = 3
MAX_RESEARCHER_ITERATIONS = 3
MAX_SEARCHES_PER_SUBAGENT = 5


def orchestrator_prompt() -> str:
    """Upstream's two orchestrator sections, joined the way upstream joins them."""
    return (
        f"Today's research date is {date.today().isoformat()}. Use this date for "
        "the report; distinguish it from publication dates of sources.\n\n"
        + deep_prompts.RESEARCH_WORKFLOW_INSTRUCTIONS
        + "\n\n" + "=" * 80 + "\n\n"
        + deep_prompts.SUBAGENT_DELEGATION_INSTRUCTIONS.format(
            max_concurrent_research_units=MAX_CONCURRENT_RESEARCH_UNITS,
            max_researcher_iterations=MAX_RESEARCHER_ITERATIONS,
            max_searches_per_subagent=MAX_SEARCHES_PER_SUBAGENT,
        )
    )


def researcher_subagent(tools: list,
                        research_dir: Optional[Path] = None) -> dict:
    """The `research-agent` sub-agent, as upstream declares it.

    `research_dir` is what makes its `/research/` listing current. A sub-agent
    gets its own copy of the filesystem tools from the framework, so it gets its
    own copy of the surface middleware too -- without it the researcher would be
    the only thing in this system still able to `grep` the project.
    """
    from langchain.agents.middleware import ToolCallLimitMiddleware

    middleware = [
        # Keep the stated budget real, but let the researcher save findings
        # and explain gaps after search is exhausted. State is per invocation.
        ToolCallLimitMiddleware(
            tool_name="tavily_search", run_limit=MAX_SEARCHES_PER_SUBAGENT,
            exit_behavior="continue"),
        tool_surface.ToolSurfaceMiddleware(),
    ]
    if research_dir is not None:
        # Parallel researchers pick their own filenames. The listing is how one
        # of them finds out the other has already taken the path it wanted.
        middleware.append(notes.ResearchNotesMiddleware(research_dir))

    return {
        "name": "research-agent",
        "description": ("Delegate research to the sub-agent researcher. Only "
                        "give this researcher one topic at a time."),
        "system_prompt": deep_prompts.RESEARCHER_INSTRUCTIONS.format(
            date=date.today().isoformat(),
            max_searches=MAX_SEARCHES_PER_SUBAGENT),
        "tools": tools,
        "middleware": middleware,
    }


def build_agent(workdir: Path, model, pool, *,
                floor: int = CONTEXT_FLOOR, members: int = 0,
                extra_middleware: Optional[Sequence] = None):
    """The compiled research orchestrator over a jailed backend and the pool."""
    from deepagents import create_deep_agent

    from agent.runtime.backend import RestrictedShellBackend

    workdir = Path(workdir)
    research_dir = workdir / RESEARCH_DIR
    research_dir.mkdir(parents=True, exist_ok=True)

    # No programs at all, unchanged. `execute` is still installed -- the backend
    # satisfies SandboxBackendProtocol either way -- but every command comes back
    # refused with the backend's own explanation.
    backend = RestrictedShellBackend(root_dir=str(workdir), allowed_programs=(),
                                     allow_git=False, allow_shell=False)

    tools = list(research_tools.make_research_tools(pool).values())

    # This project's own preamble first, then upstream's workflow. The preamble
    # carries facts about *this* system that upstream cannot know -- which pool
    # is serving the call, where the jail's `/` is, that nobody is watching --
    # and the workflow carries the method.
    #
    # No project tree. The coding agent's prompt opens with one
    # (agent/code/context.py) because it has to find its way around a repository
    # it was dropped into; this agent is *given* the question, and the paths
    # worth reading belong in the brief that asked it. A tree here is a hundred
    # lines inviting the one thing the tool surface no longer supports.
    system_prompt = prompt.build(
        floor, members=members, extra_sections=[orchestrator_prompt()])

    # Ours, after everything the framework installs: the surface middleware has
    # to run late enough to see the tools the filesystem and subagent middleware
    # inject, which is exactly where `create_deep_agent` puts caller middleware.
    middleware = [tool_surface.ToolSurfaceMiddleware(),
                  notes.ResearchNotesMiddleware(research_dir),
                  *(extra_middleware or [])]

    return create_deep_agent(
        model=model,
        # The orchestrator holds the same tools upstream gives it, and is told
        # never to use them for research. Kept rather than removed because
        # upstream keeps them, and a checked fact about a run that ignores that
        # instruction is worth more than a run that could not have disobeyed.
        tools=tools,
        system_prompt=system_prompt,
        backend=backend,
        middleware=middleware,
        subagents=[researcher_subagent(tools, research_dir)],
    )


def run_session(model, task: str, workdir: Path, pool, config=None,
                floor: int = CONTEXT_FLOOR, members: int = 0,
                trace_path: Optional[Path] = None) -> tuple:
    """Run one exploration. Returns (final_state, trace_written)."""
    config = dict(config or {})
    config.setdefault("recursion_limit", RECURSION_LIMIT)
    workdir = Path(workdir)

    jsonl = tracer_from_env()
    if jsonl is not None:
        config["callbacks"] = list(config.get("callbacks") or []) + [jsonl]

    agent = build_agent(workdir, model, pool, floor=floor, members=members)

    with collect_runs() as collected:
        final = agent.invoke({"messages": [HumanMessage(task)]}, config)

    trace_id, project_id = _trace_locator(collected.traced_runs)
    project_id = run_trace.resolve_project_id(project_id)

    written = None
    if trace_path is not None:
        tree = run_trace.fetch_tree(trace_id, project_id) if trace_id else None
        written = run_trace.write(trace_path, tree, meta={
            "trace_id": trace_id,
            "project_id": project_id,
            "workdir": str(workdir),
            "harness": "explore",
            "context_floor": floor,
            "eligible_providers": members,
            "search_accounts": len(getattr(pool, "accounts", []) or []),
            "tracing_enabled": run_trace.tracing_enabled(),
        })
        if written:
            logger.info(f"Wrote the run record to {written}")

    return final, written
