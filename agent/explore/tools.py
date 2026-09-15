"""The research agent's own tools, and the search pool behind them.

The Tavily pool lives here rather than being threaded through `build_agent`:
`tavily_search` is the only caller, and a tool that owns the account it spends
is one fewer argument on every function between here and `__main__`.

`search_pool()` is cached rather than built at import, and that is the whole of
why it is a function. `check()` raises `NoSearchPool` when no account is
configured, and importing this module must not be able to kill a process that
was never going to search -- `agent/code` imports the explorer's package to
probe whether it can delegate ([16](../../docs/16-delegation.md)). The refusal
still happens before the run rather than during it, because
[`connect()`](agent.py) asks for the pool at start-up.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger("harness.explore")

# Pages fetched per search. Upstream fetches one; one bad page is a dead end.
PAGES_PER_SEARCH = 2


@lru_cache(maxsize=1)
def search_pool():
    """The Tavily pool, built and checked once per process."""
    from llm_router import TavilyPoolRouter

    pool = TavilyPoolRouter.from_env()
    pool.check()
    return pool


def tavily_search(query: str) -> str:
    """Each result's title, URL and fetched page, as one markdown block."""
    if not query.strip():
        return "error: the query is empty."
    try:
        return search_pool().search(query.strip(),
                                    max_results=PAGES_PER_SEARCH,
                                    topic="general")
    except Exception as exc:  # noqa: BLE001 - the model reads this
        logger.warning(f"Tavily search failed: {exc!r}")
        return (f"error: the search failed ({exc}). Every Tavily account in "
                f"the pool may be out of credits; work from what you have "
                f"and say in your findings that this question is unanswered.")


def think_tool(reflection: str) -> str:
    """Record a reflection between searches."""
    return f"Reflection recorded: {reflection}"


def research_tools(workdir: Path, research_dir: str, described: dict) -> list:
    """`tavily_search`, `think_tool` and `research_status`, described by `tool_descriptions/`."""
    from langchain_core.tools import StructuredTool

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
