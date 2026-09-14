"""The research-run checks, and the trace fields they depend on.

Two layers, because they fail for different reasons and a combined failure would
not say which broke:

1. **The checks themselves** — synthetic traces, one per divergence, so a change
   to a threshold or a heuristic is visible as a named test rather than as a
   number moving in a report.
2. **The recorded run** — the numbers the first live delegation actually
   produced ([16.9](../../docs/16-delegation.md)). These are the
   regression baseline: they are not what the explorer *should* do, they are
   what it *did*, and a change meant to improve the explorer has to move them.

The fixture is derived rather than shipped whole: a 300 KB trace in the repo
would be read by nobody and diffed by everybody.
"""

import json

from evals import research_trajectory as rt


def _tool(name, args=None, output=None, end=False):
    event = {"event": "tool_end" if end else "tool_start", "tool": name}
    if end:
        event["output"] = output or ""
    else:
        event["args"] = args if args is not None else {}
    return event


def _search(query, sources=True):
    tail = ("Sources:\n[1] example.com - https://example.com" if sources
            else "Sources: none returned. Treat this as the model's own "
                 "recollection rather than something it looked up")
    return [_tool("web_search", {"query": query}),
            _tool("web_search", output="an answer\n\n" + tail, end=True)]


def _note(path="/research/topic.md", content="# topic\n"):
    return [_tool("write_file", {"content": content, "file_path": path}),
            _tool("write_file", output="ok", end=True)]


def _read(url="https://example.com"):
    return [_tool("read_url", {"url": url}),
            _tool("read_url", output="the page", end=True)]


def _named(checks):
    return {c.name: c for c in checks}


# --- the individual divergences --------------------------------------------


def test_a_run_that_never_opens_a_source_fails():
    events = [e for q in ("what is x?", "why is y?") for e in _search(q)]
    events += _note()
    assert not _named(rt.check(events))["opened a source"].ok


def test_reading_one_source_is_enough_to_pass_that_check():
    events = [e for q in ("what is x?",) for e in _search(q)]
    events += _read() + _note()
    assert _named(rt.check(events))["opened a source"].ok


def test_a_run_with_no_searches_is_not_penalised_for_not_reading():
    """Nothing was looked up, so there is nothing to have failed to open."""
    assert _named(rt.check(_note()))["opened a source"].ok


def test_going_over_the_search_budget_fails():
    events = [e for i in range(rt.CLASSIC_SEARCH_BUDGET + 1)
              for e in _search(f"what is thing {i}?")]
    assert not _named(rt.check(events))["stayed inside the search budget"].ok


def test_sitting_on_the_budget_passes():
    events = [e for i in range(rt.CLASSIC_SEARCH_BUDGET)
              for e in _search(f"what is thing {i}?")]
    assert _named(rt.check(events))["stayed inside the search budget"].ok


def test_writing_only_at_the_end_fails():
    """The observed shape: search everything, then write once."""
    events = [e for i in range(rt.CLASSIC_SEARCH_BUDGET)
              for e in _search(f"what is thing {i}?")] + _note()
    assert not _named(rt.check(events))["wrote as it went"].ok


def test_writing_early_and_updating_passes():
    events = _search("what is x?") + _note() + _search("why is y?") + _note()
    assert _named(rt.check(events))["wrote as it went"].ok


def test_a_run_that_writes_nothing_fails():
    events = [e for e in _search("what is x?")]
    assert not _named(rt.check(events))["left a deliverable"].ok


def test_scratch_files_are_not_deliverables():
    """Only `/research/*.md` counts. A run that wrote `notes.txt` at the root
    left nothing the next agent is looking for."""
    events = _search("what is x?") + [
        _tool("write_file", {"file_path": "/scratch.txt", "content": "x"}),
        _tool("write_file", output="ok", end=True)]
    assert not _named(rt.check(events))["left a deliverable"].ok


def test_an_ungrounded_search_written_up_without_reading_anything_fails():
    events = _search("what is x?", sources=False) + _note()
    assert not _named(rt.check(events))["marked its ungrounded answers"].ok


def test_an_ungrounded_search_is_forgiven_once_a_page_was_opened():
    events = _search("what is x?", sources=False) + _read() + _note()
    assert _named(rt.check(events))["marked its ungrounded answers"].ok


# --- the query-shape heuristic ---------------------------------------------


def test_keyword_queries_are_not_questions():
    """Every one of these is from the recorded run."""
    for query in (
        '"instance_id" "problem_statement" "base_commit" "test_patch"',
        '"SWE-bench: Can Language Models Resolve Real-World GitHub Issues?" '
        'dataset schema fields repo hints',
        '"environment_setup_commit" swe-bench why purpose',
        '"RepoBench" benchmark repository level dataset format tasks metrics',
    ):
        assert not rt._looks_like_a_question(query), query


def test_real_questions_are_questions():
    for query in (
        "What fields does a SWE-bench instance carry, and what is each for?",
        "Why does SWE-bench separate environment_setup_commit from base_commit?",
        "Compare the case format of SWE-bench and the Aider polyglot benchmark",
    ):
        assert rt._looks_like_a_question(query), query


def test_one_quoted_phrase_inside_a_real_question_is_still_a_question():
    assert rt._looks_like_a_question(
        'What does "environment_setup_commit" do in a SWE-bench instance?')


# --- reading a trace the tracer had to clip --------------------------------


def test_the_note_path_is_found_even_when_the_write_was_clipped():
    """A note is tens of thousands of characters and `content` sorts before
    `file_path`, so a head-only clip loses the path entirely. That is why the
    tracer keeps a tail, and why this reads the raw args as a fallback."""
    args = ("{'content': '" + "x" * 3000 + "', 'file_path': "
            "'/research/swe-bench.md'}")
    events = [{"event": "tool_start", "tool": "write_file", "args": args}]
    assert rt._note_paths(events) == ["/research/swe-bench.md"]


def test_args_recorded_as_a_python_repr_are_still_read():
    events = [{"event": "tool_start", "tool": "web_search",
               "args": "{'query': 'What is the free tier limit?'}"}]
    assert rt.from_trace(events)["question_shaped_queries"] == 1


# --- the recorded baseline --------------------------------------------------


def test_the_recorded_run_is_the_baseline_and_it_fails_four_checks():
    """The first live delegation, de-duplicated for the double-logging bug that
    run exposed. Every number here is measured; if a change to the explorer
    does not move them, it did not do anything.
    """
    assert rt.OBSERVED == {"searches": 13, "source_reads": 0,
                           "notes_written": 1,
                           "searches_before_first_note": 13,
                           "question_shaped_queries": 0}

    events = []
    for i in range(rt.OBSERVED["searches"]):
        events += _search(f'"field_{i}" "other_{i}" swe-bench schema fields')
    events += _note("/research/swe-bench-evaluation-design.md", "x" * 27_000)

    failed = {c.name for c in rt.failures(events)}
    assert failed == {"opened a source", "stayed inside the search budget",
                      "wrote as it went",
                      "asked questions rather than keywords"}


def test_every_check_carries_a_reason_a_human_can_act_on():
    for c in rt.check(_note()):
        assert c.why and len(c.why) > 40, c.name


# --- the deep-research vocabulary -------------------------------------------
# A trace has to be readable long after the agent that wrote it was replaced,
# and the two agents were given different rules. Scoring a run against rules it
# never had is the one way these checks stop meaning anything.


def _deep_search(query, results=1):
    return [_tool("tavily_search", {"query": query}),
            _tool("tavily_search", output=f"Found {results} result(s)", end=True)]


def _think(reflection="found x, still missing y"):
    return [_tool("think_tool", {"reflection": reflection}),
            _tool("think_tool", output="Reflection recorded", end=True)]


def _request(path="/research/research_request.md"):
    return [_tool("write_file", {"content": "the question", "file_path": path}),
            _tool("write_file", output="ok", end=True)]


def test_the_vocabulary_is_detected_from_the_tools_used():
    assert rt.vocabulary(_deep_search("what is x?")) == "deep"
    assert rt.vocabulary(_search("what is x?")) == "classic"
    assert rt.vocabulary(_note()) == "unknown"


def test_the_deep_agent_is_not_asked_to_have_opened_a_source():
    """`tavily_search` returns the page. There is no read_url to skip, so the
    check that caught the classic agent has nothing to catch here."""
    events = _request() + _deep_search("what is x?") + _think() + _note()
    assert "opened a source" not in {c.name for c in rt.check(events)}


def test_the_classic_agent_is_still_asked_to_have_opened_a_source():
    events = _search("what is x?") + _note()
    assert "opened a source" in {c.name for c in rt.check(events)}


def test_searching_without_reflecting_fails_the_deep_agent():
    events = _request() + [e for i in range(4)
                           for e in _deep_search(f"what is thing {i}?")] + _note()
    assert not _named(rt.check(events))["reflected between searches"].ok


def test_a_search_think_loop_passes():
    events = _request()
    for i in range(3):
        events += _deep_search(f"what is thing {i}?") + _think()
    events += _note("/research/final_report.md")
    assert _named(rt.check(events))["reflected between searches"].ok


def test_the_wiki_bookkeeping_is_not_counted_as_a_report():
    """The request, the index, the log and the open questions record the
    research, not an answer. A run that wrote only those has left no findings."""
    events = _request() + _deep_search("what is x?") + _think()
    for name in ("index.md", "log.md", "open-questions.md"):
        events += _note(f"/research/{name}")
    assert rt.from_trace(events)["reports"] == 0
    assert not _named(rt.check(events))["left a deliverable"].ok


def test_the_orchestrator_is_not_penalised_for_writing_its_report_last():
    """It synthesizes after its sub-agents return, so the report is necessarily
    the last thing written. That is upstream's design, not drift -- and it is a
    real loss of crash-resilience, recorded in docs rather than as a FAIL."""
    events = _request()
    for i in range(5):
        events += _deep_search(f"what is thing {i}?") + _think()
    events += _note("/research/final_report.md")
    assert _named(rt.check(events))["wrote as it went"].ok


def test_a_well_behaved_deep_run_fails_nothing():
    events = _request()
    for i in range(3):
        events += _deep_search(f"What is thing {i} used for?") + _think()
    events += _note("/research/overview.md") + _note("/research/log.md")
    assert rt.failures(events) == [], [str(c) for c in rt.failures(events)]


def test_a_run_nobody_logged_is_a_failure():
    """Steps 6 and 7. An unreviewed page reads exactly like a reviewed one, which
    is why the log entry that holds the review has to be counted."""
    events = _request()
    for i in range(3):
        events += _deep_search(f"What is thing {i} used for?") + _think()
    events += _note("/research/overview.md")

    assert not _named(rt.check(events))["logged the run and its review"].ok
    assert not rt.from_trace(events)["logged"]


def test_a_run_that_wrote_nothing_is_not_asked_to_log_it():
    """No deliverable is already a failure of its own; a second FAIL for not
    logging the pages it never wrote measures the same thing twice."""
    events = _request() + _deep_search("what is x?") + _think()
    assert _named(rt.check(events))["logged the run and its review"].ok


def test_an_older_runs_review_note_still_counts():
    """Before the wiki, the review was its own file, `review.md` or
    `review-<topic>.md`. Those traces still score against what they were told."""
    events = _request() + _deep_search("What is x used for?") + _think()
    events += (_note("/research/final_report-cv.md")
               + _note("/research/review-cv.md"))
    assert rt.from_trace(events)["logged"]


def test_each_agent_is_scored_against_the_budget_its_own_prompt_set():
    """The classic agent was told ten; the deep agent's prompt allows five
    searches per sub-agent across three of them. Enforcing one number on both
    scores an agent against rules it never had."""
    classic = [e for i in range(12) for e in _search(f"what is thing {i}?")]
    deep = [e for i in range(12) for e in _deep_search(f"what is thing {i}?")]

    assert rt.budget_for(classic) == rt.CLASSIC_SEARCH_BUDGET
    assert rt.budget_for(deep) == rt.DEEP_SEARCH_BUDGET
    assert not _named(rt.check(classic))["stayed inside the search budget"].ok
    assert _named(rt.check(deep))["stayed inside the search budget"].ok
