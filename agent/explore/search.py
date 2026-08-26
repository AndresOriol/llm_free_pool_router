"""The web, as two tools over the pool's own Gemini accounts.

Google's free tier grounds a Gemini call in Google Search (`google_search`) and
in named pages (`url_context`), which makes the web reachable with the keys this
project already pools -- no scraping, no search-API signup, no new secret. The
allowance is generous relative to everything else here: 5,000 grounded searches
a month across the Gemini 3.x family, and `url_context` is free outright, its
retrieved page charged only as input tokens.

**Why a tool call and not a bound tool.** Gemini 3 will happily run
`google_search` alongside ordinary function calling, so the obvious design is to
bind it to the explorer's own model and let searching happen inside a step that
was already being paid for. It is rejected for one reason: the citations come
back in `response_metadata["grounding_metadata"]`, which is not part of the
conversation. The model would search, answer from what it found, and have no
URLs to write down -- and a research note whose sources cannot be checked is
the failure mode this agent exists to avoid. Routing a search through a tool
puts the source list in a `ToolMessage`, as text the model can copy verbatim
into a file. The cost is one extra request per search, paid to make the answer
auditable.

**Why the search pool is ordered backwards.** `AutonomousLLMRouter` picks the
*highest*-priority member available, which is what you want for judgement.
Searching is not judgement: the grounded call retrieves and summarizes, and the
thinking happens afterwards, in the explorer's own conversation, on whichever
member the main pool picked. Left in priority order every search would spend one
of the reasoning tier's twenty-a-day requests on retrieval -- so `SearchRouter`
inverts the comparison and reaches for the cheapest capable member first,
climbing only as each one exhausts itself (llm_router/config.yaml).
"""

from __future__ import annotations

import logging
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool

from llm_router.router import AutonomousLLMRouter

logger = logging.getLogger("harness.explore")

# How much of a grounded answer survives into the tool message. Generous next to
# the 8,000 of agent/runtime/tools.py, because a search result *is* the payload
# here rather than a pointer to one -- and the explorer's whole job is to boil
# what comes back down into a file.
MAX_RESULT_CHARS = 12_000

# Sources listed per answer. Past a dozen the list stops being evidence and
# starts being the context budget.
MAX_SOURCES = 12

# Resolving a citation redirect is a HEAD request to a stranger's server. Short,
# because a slow source must not hold up an answer that is already written, and
# an unresolved link still gets reported.
_RESOLVE_TIMEOUT_S = 6
_USER_AGENT = "Mozilla/5.0 (compatible; free-coding-agent/1.0)"


# The two built-in tools are **not** granted together, and assuming they were
# is a mistake this file already made once. Probed against the live API on
# 2026-08-26, with both Gemma members answering `google_search` with real
# citations and both refusing `url_context`:
#
#     gemma-4-31b-it     + google_search -> 200, 6 citations
#     gemma-4-31b-it     + url_context   -> 400 INVALID_ARGUMENT
#                                           "Url Context as tool is not enabled
#                                            for this model"
#
# So the capability is per tool, not per model family, and each tool asks its
# own question. Re-probe before widening either: this is a vendor grant that
# can change without notice, and the failure mode is a member that 400s every
# call and cools down an account that was never at fault.
_SEARCH_FAMILIES = ("gemini", "gemma")
_READ_FAMILIES = ("gemini",)


def supports_google_search(provider) -> bool:
    """Can this pool member ground a call in Google Search?

    Gemma can, despite not being a Gemini model, and it matters more than any
    other member here: it carries 1,500 requests a day against the flash tier's
    twenty (llm_router/config.yaml). Excluding it -- which this did at first, by
    reasoning from the model family instead of asking the API -- threw away most
    of the pool's actual search capacity.
    """
    return (provider.platform == "gemini"
            and str(provider.model).lower().startswith(_SEARCH_FAMILIES))


def supports_url_context(provider) -> bool:
    """Can this pool member be pointed at a URL and read it?

    Narrower than the above: Gemini only. Gemma refuses with a 400, which is a
    non-transient error, so a Gemma member in the read pool would not reroute --
    it would surface as a failed tool call every single time.
    """
    return (provider.platform == "gemini"
            and str(provider.model).lower().startswith(_READ_FAMILIES))


class SearchRouter(AutonomousLLMRouter):
    """The same provider objects as the main pool, reached cheapest-first.

    Sharing the objects rather than copying them is the point: cooldown lives on
    the provider, so a search that exhausts an account is immediately visible to
    the conversation routing through the same account, and vice versa. Two
    routers over one set of members is the honest model of one free tier being
    spent two ways.

    The context arguments are accepted and ignored. A search prompt is a
    sentence, so no member is ever too narrow for it, and a floor inherited from
    the caller would only exclude members that could have served it.
    """

    def get_best_provider(self, estimated_tokens=None, min_context=None,
                          strict_context=False):
        available = [p for p in self.providers if p.check_availability()]
        if not available:
            return None
        return max(available, key=lambda p: p.priority)


def _pool(router, predicate) -> Optional[SearchRouter]:
    members = [p for p in router.providers if predicate(p)]
    return SearchRouter(members) if members else None


def search_pool(router) -> Optional[SearchRouter]:
    """Members that can ground a call in Google Search, or None."""
    return _pool(router, supports_google_search)


def read_pool(router) -> Optional[SearchRouter]:
    """Members that can read a named URL, or None."""
    return _pool(router, supports_url_context)


def pools(router) -> tuple:
    """`(search_pool, read_pool)`, either of which may be None.

    Two pools rather than one because the two tools are granted separately, and
    the wider of them is the one that matters: Gemma is in the search pool and
    not the read pool, and Gemma is where the request budget is.
    """
    searching, reading = search_pool(router), read_pool(router)
    logger.info(
        f"of {len(router.providers)} pool members, "
        f"{len(searching.providers) if searching else 0} can search the web and "
        f"{len(reading.providers) if reading else 0} can read a URL.")
    return searching, reading


def _text(message: Any) -> str:
    """The reply as plain text, whether it arrived as a string or as blocks.

    Gemini 3 returns content blocks once thinking is involved, and a research
    answer read as `str(list_of_dicts)` is unreadable rather than merely ugly.
    """
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in (None, "text"):
                parts.append(str(block.get("text", "")))
        return "\n".join(p for p in parts if p)
    return str(content)


def _real_url(url: str) -> str:
    """The page a grounding citation actually points at.

    Every `uri` in `grounding_metadata` is a
    `vertexaisearch.cloud.google.com/grounding-api-redirect/...` link, not the
    source. Written into a research note unresolved it is worthless twice over:
    a human cannot see where the claim came from without following it, and
    `read_url` cannot open it. One HEAD request turns it back into
    `https://ai.google.dev/gemini-api/docs/pricing`, which is the whole value of
    a citation.

    Best effort by design. A redirect that will not resolve -- expired, blocked,
    offline -- yields the original link, which is worse than the real URL and
    much better than dropping the source.
    """
    try:
        request = urllib.request.Request(
            url, method="HEAD", headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(request, timeout=_RESOLVE_TIMEOUT_S) as response:
            return response.url or url
    except Exception as exc:  # noqa: BLE001 - any failure keeps the redirect
        logger.debug(f"Could not resolve {url[:60]}...: {exc!r}")
        return url


def _sources(message: Any) -> list:
    """`(title, url)` for every page the grounded answer drew on.

    Read out of `grounding_metadata`, which is where the API puts the citations
    and where the model itself cannot see them. Lifting them into the tool
    result is the whole reason this is a tool.

    The redirects are resolved in parallel because they are independent waits on
    other people's servers: a dozen of them in sequence is a minute of an
    agent's run spent on HTTP round trips, and the run is on a clock.
    """
    metadata = getattr(message, "response_metadata", None) or {}
    grounding = metadata.get("grounding_metadata") or {}

    listed, seen = [], set()
    for chunk in (grounding.get("grounding_chunks") or []):
        web = (chunk or {}).get("web") or {}
        url = web.get("uri")
        if not url or url in seen:
            continue
        seen.add(url)
        # `title` is the source's domain and `domain` is usually null, so the
        # title is the only human-readable label there is.
        listed.append((web.get("title") or web.get("domain") or "source", url))
        if len(listed) >= MAX_SOURCES:
            break
    if not listed:
        return []

    with ThreadPoolExecutor(max_workers=min(len(listed), 8)) as pool:
        resolved = list(pool.map(_real_url, [url for _, url in listed]))
    return [(title, url) for (title, _), url in zip(listed, resolved)]


def _queries(message: Any) -> list:
    """The searches Google actually ran, which are rarely the words asked for."""
    metadata = getattr(message, "response_metadata", None) or {}
    grounding = metadata.get("grounding_metadata") or {}
    return list(grounding.get("web_search_queries") or [])


def _render(message: Any, note: str = "") -> str:
    body = _text(message).strip()
    if not body:
        # Observed in practice: a grounded call that searches, returns its
        # citations, and answers with nothing. Naming the finish reason is the
        # difference between the agent retrying a rephrased query and the agent
        # concluding the web has nothing to say on the subject.
        reason = (getattr(message, "response_metadata", None) or {}).get(
            "finish_reason", "unknown")
        body = (f"(the model searched but returned no text; finish_reason="
                f"{reason}. The sources below are still real -- read one with "
                f"read_url, or ask a narrower question.)")
    if len(body) > MAX_RESULT_CHARS:
        body = body[:MAX_RESULT_CHARS] + "\n...(truncated)"

    parts = [body]
    queries = _queries(message)
    if queries:
        parts.append("Searched for: " + "; ".join(queries[:MAX_SOURCES]))

    sources = _sources(message)
    if sources:
        listed = "\n".join(f"[{i}] {title} - {url}"
                           for i, (title, url) in enumerate(sources, 1))
        parts.append("Sources:\n" + listed)
    else:
        parts.append("Sources: none returned. Treat this as the model's own "
                     "recollection rather than something it looked up, and say "
                     "so if you write it down.")

    if note:
        parts.append(note)
    return "\n\n".join(parts)


def _bound(pool: Optional[SearchRouter], tool: dict, max_retries: int):
    """A model over `pool` with one built-in tool bound, or None if no pool.

    Built on `RouterChatModel` rather than on a provider directly, so a web call
    inherits the same failover the rest of the project runs on: an account
    rate-limited mid-research costs one reroute, not the run.

    **One tool per model, never both.** Binding `google_search` and
    `url_context` together lets a Gemini member search and then read what it
    found, which is better -- and it 400s on Gemma, which would cost the search
    pool the members holding 1,500 requests a day. Two narrow bindings beat one
    wide one that excludes the cheapest capacity in the pool.
    """
    if pool is None:
        return None
    from agent.runtime.chat_model import RouterChatModel

    model = RouterChatModel(
        router=pool, max_retries=max(max_retries, len(pool.providers) + 3))
    return model.bind_tools([tool])


def make_search_tools(searching: Optional[SearchRouter],
                      reading: Optional[SearchRouter] = None,
                      max_retries: int = 6) -> dict:
    """The two web tools. Returns name -> tool.

    `reading` may be None -- a pool of nothing but Gemma can search and cannot
    open a page -- in which case `read_url` says so instead of failing.
    """
    searcher = _bound(searching, {"google_search": {}}, max_retries)
    reader = _bound(reading, {"url_context": {}}, max_retries)

    def _ask(bound, instruction: str, note: str = "") -> str:
        try:
            reply = bound.invoke([HumanMessage(instruction)])
        except Exception as exc:  # noqa: BLE001 - the agent reads this and retries
            logger.warning(f"Web call failed: {exc!r}")
            return (f"error: the web call failed ({exc}). The pool may be out of "
                    f"quota for now; try again later or work from what you have.")
        return _render(reply, note)

    def web_search(query: str) -> str:
        """Search the web and return a grounded summary with its source URLs.

        Ask a full question, not keywords. Returns an answer plus a numbered
        list of the pages it came from — always cite those URLs in your notes.
        """
        if not query.strip():
            return "error: the query is empty."
        return _ask(searcher, (
            "Search the web and answer the following, using current sources. "
            "Be specific and concrete: prefer names, numbers, versions and dates "
            "over generalities, and say plainly when the sources disagree or "
            "when you could not find an answer.\n\n"
            f"{query.strip()}"))

    def read_url(url: str, question: str = "") -> str:
        """Read one web page and summarize it, optionally answering a question.

        Use after `web_search` to go deeper on a page worth more than its
        snippet. Paywalled pages, YouTube and private URLs will not load.
        """
        url = url.strip()
        if not url:
            return "error: the url is empty."
        if reader is None:
            return ("error: no member of this pool can open a URL -- reading a "
                    "named page needs a `gemini-*` model and this pool has "
                    "none. Use web_search instead and cite what it returns.")
        asked = question.strip() or "Summarize what this page says."
        return _ask(reader, (
            f"Read {url} and answer from its actual content. If the page could "
            f"not be retrieved, say so plainly rather than answering from "
            f"memory.\n\n{asked}"),
            note=f"Read from: {url}")

    fns = [web_search, read_url]
    return {f.__name__: StructuredTool.from_function(f, name=f.__name__,
                                                     description=f.__doc__)
            for f in fns}
