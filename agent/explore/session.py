"""One exploration over one workdir: the web in, files out.

The same machinery as the coding agent (agent/code) with three deliberate
differences, and nothing else changed -- the loop, the compaction, the jail and
the failover are shared, because two agents diverging on those would be two
things to debug rather than one.

1. **It can reach the web.** `web_search` and `read_url`, grounded through the
   pool's own Gemini members (agent/explore/search.py).
2. **It cannot run the project.** The coding agent needs `python`/`pytest`/`git`
   to close its own loop -- write a test, run it, react. A researcher has no
   loop to close, so the allowlist is empty and `execute` refuses everything.
   Nothing is lost and the blast radius of an unattended run drops to the files
   it writes.
3. **Its deliverable is on disk.** The prompt says so at length
   (system_prompt.md), because the reader is the next agent to open this
   directory rather than whoever launched this one.

The handoff between the two is the filesystem and nothing else. The explorer
writes `/research/*.md` into a workdir; the coding agent, pointed at that same
workdir, reads them like any other file. There is no shared state, no message
bus and no protocol to keep in step -- which is the only reason it is safe to
run them hours apart.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Sequence

from langchain_core.messages import HumanMessage
from langchain_core.tracers.context import collect_runs

from agent.code import context
from agent.code import trace as run_trace
from agent.code.session import CONTEXT_FLOOR, RECURSION_LIMIT, _trace_locator
from agent.explore import prompt, search
from agent.runtime.trace import tracer_from_env

logger = logging.getLogger("harness.explore")

# Where the notes go unless the task says otherwise. Stated in the prompt and
# here, because the launcher creates it: an agent that has to `mkdir` before its
# first write spends a step discovering that, and sometimes writes to `/` instead.
RESEARCH_DIR = "research"


class NoSearchPool(SystemExit):
    """Raised before the run when nothing in the pool can reach the web."""


def build_agent(workdir: Path, model, web, *,
                floor: int = CONTEXT_FLOOR, members: int = 0,
                extra_middleware: Optional[Sequence] = None):
    """The compiled explorer over a jailed backend and the web pools.

    `web` is the `(search_pool, read_pool)` pair from `check_search`.
    """
    from deepagents import create_deep_agent
    from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT

    from agent.runtime.backend import RestrictedShellBackend

    workdir = Path(workdir)
    (workdir / RESEARCH_DIR).mkdir(parents=True, exist_ok=True)

    # No programs at all. `execute` is still installed -- the backend satisfies
    # SandboxBackendProtocol either way, and the SDK reads that, not the
    # allowlist -- but every command comes back refused with the backend's own
    # explanation, which is a readable tool result rather than an exception. So
    # the coding agent's ShellAllowListMiddleware buys nothing here: there is no
    # allowlist for it to mirror.
    backend = RestrictedShellBackend(root_dir=str(workdir), allowed_programs=(),
                                     allow_git=False, allow_shell=False)

    project = context.section(workdir)
    system_prompt = prompt.build(floor, members=members,
                                 extra_sections=[project] if project else None)

    tools = list(search.make_search_tools(*web).values())

    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        backend=backend,
        middleware=list(extra_middleware or []),
        subagents=[GENERAL_PURPOSE_SUBAGENT],
    )


def check_search(router) -> tuple:
    """Fail before the run rather than during it. Returns `(search, read)`.

    Only the *search* pool is required. Without one, every `web_search` would
    come back an error and the agent would spend its whole budget writing a
    research note about having no research -- a failure that reads like a bad
    model rather than a misconfigured pool.

    An empty *read* pool is survivable: the agent can still search, and
    `read_url` says why it cannot open a page. It is worth a warning, because a
    pool of nothing but Gemma searches perfectly well and can never follow a
    citation.
    """
    searching, reading = search.pools(router)
    if searching is None:
        raise NoSearchPool(
            "No member of the pool can search the web. Grounding with Google "
            "Search is a Gemini API feature, so this agent needs at least one "
            "`gemini-*` or `gemma-*` model on a `gemini` account in "
            "llm_router/config.yaml.")
    if reading is None:
        logger.warning(
            "No member of the pool can open a URL (that needs a `gemini-*` "
            "model; Gemma refuses `url_context`). The agent can search but "
            "cannot follow a citation to its source.")
    return searching, reading


def run_session(model, task: str, workdir: Path, web, config=None,
                floor: int = CONTEXT_FLOOR, members: int = 0,
                trace_path: Optional[Path] = None) -> tuple:
    """Run one exploration. Returns (final_state, trace_written)."""
    config = dict(config or {})
    config.setdefault("recursion_limit", RECURSION_LIMIT)
    workdir = Path(workdir)

    jsonl = tracer_from_env()
    if jsonl is not None:
        config["callbacks"] = list(config.get("callbacks") or []) + [jsonl]

    agent = build_agent(workdir, model, web, floor=floor, members=members)

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
            "search_providers": len(web[0].providers) if web[0] else 0,
            "read_providers": len(web[1].providers) if web[1] else 0,
            "tracing_enabled": run_trace.tracing_enabled(),
        })
        if written:
            logger.info(f"Wrote the run record to {written}")

    return final, written
