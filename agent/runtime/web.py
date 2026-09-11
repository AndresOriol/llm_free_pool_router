"""The web as one call: Tavily finds URLs, each page is fetched and converted,
and the page itself -- not a summary of it -- is what comes back.

The explorer's `tavily_search` is this ([agent/explore/agent.py](../explore/agent.py)).
Why the page rather than a snippet is [15.3](../../docs/15-explorer.md#153-why-the-search-tool-fetches-the-page);
which account serves a search is `llm_router.TavilyPoolRouter`
([15.4](../../docs/15-explorer.md#154-which-account-serves-a-search)).

Every failure comes back as text rather than an exception: the caller is a model
deciding what to search next, and "this page 403s" is something it can act on.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("harness.explore")

# ~5,000 tokens a page: a reference page arrives whole, and three of them do not
# evict the conversation carrying them (15.3.1).
MAX_PAGE_CHARS = 20_000
FETCH_TIMEOUT = 10.0

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")


def fetch_page(url: str, timeout: float = FETCH_TIMEOUT) -> str:
    """One page as markdown, or a readable explanation of why not."""
    import httpx
    from markdownify import markdownify

    try:
        response = httpx.get(url, headers={"User-Agent": _UA}, timeout=timeout,
                             follow_redirects=True)
        response.raise_for_status()
        text = markdownify(response.text)
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        logger.info(f"Could not fetch {url}: {exc!r}")
        return (f"(could not fetch this page: {exc}. The URL and title above "
                f"are still real -- cite them only for what the search snippet "
                f"supports, or find another source.)")

    if len(text) > MAX_PAGE_CHARS:
        return (text[:MAX_PAGE_CHARS]
                + f"\n\n...(page truncated at {MAX_PAGE_CHARS:,} of "
                  f"{len(text):,} characters. Search for a narrower question if "
                  f"what you need was further down.)")
    return text


def search(pool, query: str, max_results: int = 2) -> str:
    """Each result's title, URL and fetched page, as one markdown block."""
    if not query.strip():
        return "error: the query is empty."
    try:
        results = pool.search(query.strip(), max_results=max_results,
                              topic="general")
    except Exception as exc:  # noqa: BLE001 - the model reads this
        logger.warning(f"Tavily search failed: {exc!r}")
        return (f"error: the search failed ({exc}). Every Tavily account in "
                f"the pool may be out of credits; work from what you have "
                f"and say in your findings that this question is unanswered.")

    hits = results.get("results") or []
    if not hits:
        return (f"No results for {query!r}. Rephrase it as a different "
                f"question rather than repeating this one.")

    blocks = []
    for hit in hits:
        url, title = hit.get("url", ""), hit.get("title", "(untitled)")
        blocks.append(f"## {title}\n**URL:** {url}\n\n{fetch_page(url)}\n\n---\n")
    return f"Found {len(blocks)} result(s) for '{query}':\n\n" + "\n".join(blocks)


class NoSearchPool(SystemExit):
    """Raised before the run when no Tavily account is configured."""


def check_pool(pool) -> int:
    """Refuse before the run rather than during it. Returns the account count.

    Without a way to search, a research agent spends its whole budget writing a
    note about having no research.
    """
    count = len(getattr(pool, "accounts", []) or [])
    if not count:
        raise NoSearchPool(
            "No Tavily account is configured, so this agent cannot search. Put "
            "at least one key in llm_router/.env as TAVILY_API_KEY_1 "
            "(TAVILY_API_KEY_2, _3, ... are picked up automatically).")
    if count == 1:
        logger.warning(
            "Only one Tavily account is configured. Search still works and has "
            "nothing to fail over to: when its monthly credits run out, every "
            "search fails until the next reset.")
    logger.info(f"{count} Tavily account(s) in the search pool.")
    return count
