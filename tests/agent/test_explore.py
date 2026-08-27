"""The explorer's configuration, checked without the network.

Everything here is about what the agent is *told*, what it is *allowed*, and
which pool members it spends. Whether it then does good research is a question
for a real run, not for this file.

    python -m pytest tests/agent/test_explore.py
"""

import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langchain_core.messages import AIMessage

from agent.explore import prompt, search, session
from agent.runtime.backend import RestrictedShellBackend
from llm_router.router import AutonomousLLMRouter


class _Provider:
    """The four fields the search pool reads off a pool member."""

    def __init__(self, model, platform="gemini", priority=10, available=True):
        self.model = model
        self.platform = platform
        self.priority = priority
        self.name = f"{model}_acct"
        self.max_input_tokens = 250_000
        self.available = available

    def check_availability(self):
        return self.available


def _grounded(text, sources=(), queries=(), finish_reason="STOP"):
    """An AIMessage shaped like a real grounded reply, minus the network."""
    return AIMessage(content=text, response_metadata={
        "finish_reason": finish_reason,
        "grounding_metadata": {
            "web_search_queries": list(queries),
            # Real citations are opaque redirects with the domain as the title.
            "grounding_chunks": [{"web": {"uri": uri, "title": title}}
                                 for title, uri in sources],
        }})


# -- What the model is told -------------------------------------------------

def test_prompt_leaves_no_placeholder_unreplaced():
    text = prompt.build(128_000, members=9)
    assert not re.findall(r"\{[a-z_]+\}", text)


def test_prompt_names_the_web_tools_and_the_deliverable():
    text = prompt.build(128_000, members=9)
    assert "web_search" in text and "read_url" in text
    # The whole point of this agent: the files are the output, not the reply.
    assert "/research/" in text
    assert "128,000 input tokens" in text


# -- Which members can actually search --------------------------------------

def test_gemma_can_search_but_cannot_read_a_url():
    # Probed against the live API: Gemma answers `google_search` with real
    # citations and refuses `url_context` with a 400. The two grants are
    # separate, and assuming otherwise cost the search pool its cheapest
    # members -- Gemma carries 1,500 requests a day against flash's twenty.
    gemma = _Provider("gemma-4-31b-it")
    assert search.supports_google_search(gemma)
    assert not search.supports_url_context(gemma)


def test_gemini_models_can_do_both():
    gemini = _Provider("gemini-3.5-flash-lite")
    assert search.supports_google_search(gemini)
    assert search.supports_url_context(gemini)


def test_groq_can_do_neither():
    groq = _Provider("openai/gpt-oss-120b", platform="groq")
    assert not search.supports_google_search(groq)
    assert not search.supports_url_context(groq)


def test_the_two_pools_differ_by_exactly_gemma():
    gemini, gemma = _Provider("gemini-3.5-flash-lite"), _Provider("gemma-4-31b-it")
    searching, reading = search.pools(
        AutonomousLLMRouter([gemini, gemma, _Provider("x", platform="groq")]))
    assert searching.providers == [gemini, gemma]
    assert reading.providers == [gemini]


def test_search_pool_holds_the_same_objects_as_the_main_pool():
    # Not copies: cooldown lives on the provider, so a search that exhausts an
    # account has to be visible to the conversation sharing it.
    gemini = _Provider("gemini-3.5-flash-lite")
    router = AutonomousLLMRouter([gemini, _Provider("x", platform="groq")])
    pool = search.search_pool(router)
    assert pool.providers == [gemini]
    assert pool.providers[0] is gemini


def test_search_reaches_for_the_cheapest_member_first():
    # Priority 1 is the reasoning tier at twenty requests a day. Retrieval must
    # not spend it while a flash-lite is warm.
    reasoning = _Provider("gemini-3.6-flash", priority=1)
    cheap = _Provider("gemini-3.5-flash-lite", priority=22)
    # Gemma is last-resort for judgement and *first* for retrieval: 1,500
    # requests a day is the largest budget in the pool by two orders of scale.
    plentiful = _Provider("gemma-4-31b-it", priority=31)
    pool = search.search_pool(AutonomousLLMRouter([reasoning, cheap, plentiful]))
    assert pool.get_best_provider() is plentiful

    plentiful.available = False
    assert pool.get_best_provider() is cheap

    cheap.available = False
    assert pool.get_best_provider() is reasoning

    reasoning.available = False
    assert pool.get_best_provider() is None


def test_a_pool_that_cannot_search_is_refused_before_the_run():
    router = AutonomousLLMRouter([_Provider("openai/gpt-oss-120b",
                                            platform="groq")])
    assert search.search_pool(router) is None
    try:
        session.check_search(router)
    except session.NoSearchPool as exc:
        assert "gemini" in str(exc).lower()
    else:
        raise AssertionError("a pool that cannot search must refuse up front")


def test_a_gemma_only_pool_searches_and_says_why_it_cannot_read():
    # Survivable, not fatal: searching still works, and `read_url` has to
    # explain itself rather than 400 on every call.
    router = AutonomousLLMRouter([_Provider("gemma-4-31b-it")])
    searching, reading = session.check_search(router)
    assert searching is not None and reading is None

    tools = search.make_search_tools(searching, reading)
    answer = tools["read_url"].invoke({"url": "https://example.com"})
    assert "error" in answer and "gemini" in answer


# -- What comes back from a search ------------------------------------------

def test_sources_are_listed_and_deduplicated(monkeypatch):
    # Resolution is a HEAD request per citation; stub it, this test is offline.
    monkeypatch.setattr(search, "_real_url", lambda url: url.replace(
        "https://redirect/", "https://real/"))
    rendered = search._render(_grounded(
        "Twenty per request.",
        sources=[("docs.google.dev", "https://redirect/a"),
                 ("dupe", "https://redirect/a"),
                 ("medium.com", "https://redirect/b")],
        queries=["gemini url_context url limit"]))
    assert "Twenty per request." in rendered
    assert "Searched for: gemini url_context url limit" in rendered
    assert "[1] docs.google.dev - https://real/a" in rendered
    assert "[2] medium.com - https://real/b" in rendered
    assert "[3]" not in rendered


def test_an_unresolvable_redirect_is_still_reported(monkeypatch):
    # A citation that will not resolve is worse than a real URL and far better
    # than a silently dropped source.
    monkeypatch.setattr(search, "_real_url", lambda url: url)
    rendered = search._render(_grounded("x", sources=[("a.com", "https://redirect/a")]))
    assert "https://redirect/a" in rendered


def test_an_ungrounded_answer_says_so():
    # No sources means the model answered from memory. The agent must be told,
    # or it launders a recollection into a cited fact.
    rendered = search._render(_grounded("I believe it is twenty."))
    assert "recollection" in rendered


def test_an_empty_answer_carries_its_finish_reason(monkeypatch):
    monkeypatch.setattr(search, "_real_url", lambda url: url)
    rendered = search._render(_grounded(
        "", sources=[("a.com", "https://redirect/a")], finish_reason="MAX_TOKENS"))
    assert "finish_reason=MAX_TOKENS" in rendered
    # The sources survive an empty answer: they are what makes it recoverable.
    assert "https://redirect/a" in rendered


def test_content_blocks_are_flattened_to_text():
    # Gemini 3 returns blocks once thinking is involved.
    message = AIMessage(content=[{"type": "text", "text": "first"},
                                 {"type": "text", "text": "second"}])
    assert search._text(message) == "first\nsecond"


def test_an_empty_query_never_reaches_the_pool():
    router = AutonomousLLMRouter([_Provider("gemini-3.5-flash-lite")])
    tools = search.make_search_tools(*search.pools(router))
    assert "error" in tools["web_search"].invoke({"query": "   "})
    assert "error" in tools["read_url"].invoke({"url": ""})


# -- What it is allowed to do ------------------------------------------------

def test_the_explorer_runs_no_programs_at_all():
    root = Path(tempfile.mkdtemp())
    backend = RestrictedShellBackend(root_dir=str(root), allowed_programs=())
    result = backend.execute("python -c 'print(1)'")
    assert result.exit_code == 1
    # Refused readably, as a tool result rather than an exception -- and without
    # the "only runs []" that reads to a model like a bug to work around.
    assert "no shell here" in result.output
    assert "[]" not in result.output


def test_the_explorer_still_reads_and_writes_files():
    # The handoff to the coding agent is the filesystem, so this is the one
    # capability the explorer cannot lose.
    root = Path(tempfile.mkdtemp())
    backend = RestrictedShellBackend(root_dir=str(root), allowed_programs=())
    backend.write("/research/notes.md", "# what I found\n")
    assert (root / "research" / "notes.md").read_text() == "# what I found\n"
    assert "what I found" in (backend.read("/research/notes.md")
                              .file_data or {}).get("content", "")
