"""`tavily_search` and `think_tool`, ported onto the account pool.

Source: `langchain-ai/deepagents-quickstarts`, `deep_research/research_agent/
tools.py` (MIT). The shape is upstream's and the two changes are ours.

**Why this replaces the grounded-Gemini search.** The old `web_search` asked a
Gemini model to search and returned *its summary*; opening the actual page was a
separate `read_url` tool the agent could skip. It skipped it every time -- a
recorded run made 13 searches and 0 reads, and wrote a report of exact figures
none of which had been traced to a source
([15.7](../../docs/15-explorer.md#157-measured-against-a-reference-research-agent)).

Upstream's tool does not have that failure available: Tavily discovers URLs, the
tool fetches each page over HTTP and converts it to markdown, and what reaches
the model *is* the page. Reading is not a choice the agent gets to make. That is
the structural fix, and it is why this branch exists.

### The two adaptations

1. **The pool, not a client.** Upstream constructs one `TavilyClient()`. Search
   here goes through `TavilyPoolRouter`, so a key that hits its monthly credit
   wall fails over to the next account instead of ending the run -- the same
   argument the model pool rests on ([3. The pool model](../../docs/03-pool-model.md)).
2. **Pages are clipped.** Upstream returns whole pages. A documentation page
   converts to tens of thousands of characters and the conversation carrying it
   is routed against a 128,000-token floor, so one unlucky fetch could displace
   everything the agent had already learned. Each page is cut at
   `MAX_PAGE_CHARS`, and the cut says so, which is the difference between a
   truncated source and a source that appears to end mid-sentence.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("harness.explore")

# ~5,000 tokens a page. Big enough that a reference page arrives whole and small
# enough that three of them do not evict the conversation.
MAX_PAGE_CHARS = 20_000
# Upstream defaults to 1. Two costs one extra HTTP fetch and no model call, and
# a single result is one bad page away from a dead end.
DEFAULT_MAX_RESULTS = 2
FETCH_TIMEOUT = 10.0

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")


def fetch_page(url: str, timeout: float = FETCH_TIMEOUT) -> str:
    """One page as markdown, or a readable explanation of why not.

    Errors come back as text rather than raising: the caller is a model deciding
    what to search next, and "this page 403s" is information it can act on,
    where a traceback ends the run.
    """
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


def make_research_tools(pool, *, max_results: int = DEFAULT_MAX_RESULTS):
    """`{name: tool}` for the researcher sub-agent, bound to one Tavily pool."""
    from langchain_core.tools import StructuredTool

    def tavily_search(query: str) -> str:
        """Search the web and return the full text of the pages found.

        This is the only way anything from outside this workspace reaches you.
        It searches, then opens every result and converts it to markdown, so
        what comes back is the page itself — there is no summary in between and
        no second tool to open a source with. Each result arrives as its title,
        its URL, and its content; a page too long to return whole is cut and
        says where it was cut.

        Ask a whole question, not a string of keywords: `"tavily api pricing
        free tier"` retrieves, but "What does Tavily's free tier include per
        month, and what happens at the limit?" retrieves *and* tells the engine
        what you are trying to learn.

        - Cite the URL this returned, exactly, next to the claim it supports.
          Never write down a plausible-looking address you did not receive.
        - A page that would not open comes back saying so. That is information —
          record it as a source you could not read, do not cite it for figures.
        - One call costs a search credit from a small monthly pool and a model
          call from a daily one. Repeating a question you have already asked
          costs the same as asking the next one.
        """
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

        return (f"Found {len(blocks)} result(s) for '{query}':\n\n"
                + "\n".join(blocks))

    def think_tool(reflection: str) -> str:
        """Stop and examine your own research before continuing it.

        Use after every search, before deciding whether to search again. It
        retrieves nothing and changes nothing; it is a step that exists purely
        so the decision to keep going is made deliberately once, rather than by
        default a dozen times.

        Write, in plain sentences:

        - **What this search actually established** — the specific fact, with
          the page it came from. "Useful background" is not a finding.
        - **What it did not.** Name the question that is still open, and whether
          the last two searches have started returning the same thing.
        - **Where the answer is weakest.** Which claim rests on a single source,
          a vendor's own page, or your inference rather than something you read.
          That is what the next search should attack — not the part you have
          already confirmed twice.
        - **Whether you are done.** Say it either way. Enough evidence to answer,
          with the gaps named, is a finished job; a run that keeps searching
          because it has budget left is spending someone's day of requests on
          confirmation.
        """
        return f"Reflection recorded: {reflection}"

    return {
        "tavily_search": StructuredTool.from_function(
            func=tavily_search, name="tavily_search",
            description=tavily_search.__doc__),
        "think_tool": StructuredTool.from_function(
            func=think_tool, name="think_tool",
            description=think_tool.__doc__),
    }


class NoSearchPool(SystemExit):
    """Raised before the run when no Tavily account is configured."""


def check_pool(pool) -> int:
    """Fail before the run rather than during it. Returns the account count.

    The same argument as the model pool's floor check
    ([6](../../docs/06-agent.md)): without a way to search, this agent would
    spend its whole budget writing a research note about having no research.
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
