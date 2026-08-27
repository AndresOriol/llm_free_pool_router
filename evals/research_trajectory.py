"""What a research run *did*, as numbers a regression can be pinned to.

`metrics.from_trace` counts a coding run: steps, tokens, whether the tests were
run, whether a failure was self-corrected. None of that discriminates a good
research run from a bad one, because a research agent's deliverable is a file
full of claims and its failures are all failures of *method*: it can search
thirteen times, cite real sources, write a report that reads beautifully, and
still never have opened a single page it quoted.

That run happened ([11. Evaluation status](../docs/11-eval-status.md)), and none
of the existing metrics moved. So these exist.

**Every check here is a divergence observed in a recorded run, not a rule
invented in advance.** Each one names what the explorer's own prompt or tool
description already tells it to do
([agent/explore/system_prompt.md](../agent/explore/system_prompt.md)), so a
failing check is the agent drifting from its instructions rather than this file
having an opinion. The thresholds are deliberately loose: they are there to
catch a *regression*, not to score a run.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

# Two vocabularies, because there are two research agents and a trace has to be
# readable long after the one that wrote it was replaced. `web_search`/`read_url`
# is the grounded-Gemini pair; `tavily_search`/`think_tool` is the deep-research
# port (docs/15-explorer.md#158). Which checks apply depends on which the run
# used, and guessing wrong scores an agent against rules it was never given.
CLASSIC_SEARCH = "web_search"
DEEP_SEARCH = "tavily_search"
SEARCH_TOOLS = {CLASSIC_SEARCH, DEEP_SEARCH}
READ_TOOLS = {"read_url"}
REFLECT_TOOLS = {"think_tool"}
WRITE_TOOLS = {"write_file", "edit_file"}
REQUEST_NOTE = "research_request.md"

# A note is a research deliverable; anything else the agent writes is scratch.
_NOTE = re.compile(r"/research/.+\.md$", re.IGNORECASE)
_NOTE_IN_TEXT = re.compile(r"/research/[\w./-]+\.md", re.IGNORECASE)
# The marker `_render` appends when a grounded call came back with nothing to
# cite (agent/explore/search.py). Reading it needs the trace to keep a tail,
# which is why `_clip` keeps one (agent/runtime/trace.py).
_NO_SOURCES = "Sources: none returned"


def _args(event: dict) -> dict:
    """A tool call's arguments, whatever shape the tracer recorded them in."""
    raw = event.get("args")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        for parse in (json.loads, _literal_eval):
            try:
                value = parse(raw)
            except Exception:  # noqa: BLE001 - a best-effort read of a log field
                continue
            if isinstance(value, dict):
                return value
    return {}


def _literal_eval(text: str):
    import ast

    return ast.literal_eval(text)


def _calls(events: list, tools: set) -> list:
    return [e for e in events if e.get("event") == "tool_start"
            and e.get("tool") in tools]


def _note_path(event: dict) -> Optional[str]:
    """The `/research/*.md` path one write touched, or None.

    Reads the parsed `file_path` when there is one and falls back to scanning
    the raw args. The fallback is not defensive programming: a write carries the
    whole file in `content`, the tracer clips a field it cannot afford to store
    whole, and `content` sorts before `file_path` -- so on a long note the path
    is simply not in the record. That is why the tracer now keeps a tail
    (agent/runtime/trace.py), and why this reads what old traces actually have.
    """
    path = _args(event).get("file_path")
    if path and _NOTE.search(str(path)):
        return str(path)
    found = _NOTE_IN_TEXT.search(str(event.get("args") or ""))
    return found.group(0) if found else None


def _note_paths(events: list) -> list:
    """Every `/research/*.md` path written, in order, with duplicates kept."""
    return [p for p in (_note_path(e) for e in _calls(events, WRITE_TOOLS)) if p]


_INTERROGATIVE = ("what", "why", "how", "which", "who", "when", "where",
                  "does", "do", "did", "is", "are", "can", "should", "list",
                  "describe", "explain", "compare")


def _looks_like_a_question(query: str) -> bool:
    """Whether a query reads as a sentence rather than as search operators.

    `web_search`'s own description says *"Ask a full question, not keywords"*,
    because the grounded call *answers* a question and only *retrieves* against
    keywords. A query of quoted operators is the agent talking to a search
    engine that is not there, and it throws away the half of the call that
    reasons.

    Word count alone is not the test, and a first attempt that used it scored
    half of a recorded run's keyword queries as questions: one quoted phrase
    plus five bare nouns is still a keyword query. What separates them is an
    interrogative opening or a question mark, so that is what this asks.
    """
    text = (query or "").strip()
    if not text:
        return False
    if len(re.findall(r'"[^"]+"', text)) > 1:
        return False           # two or more quoted operators: a search string
    stripped = re.sub(r'"[^"]*"', " ", text).strip()
    if len(stripped.split()) < 4:
        return False
    return (text.endswith("?")
            or stripped.split()[0].lower().rstrip(",") in _INTERROGATIVE)


def from_trace(events: list) -> dict:
    """Counts over one research run's event stream."""
    searches = _calls(events, SEARCH_TOOLS)
    reads = _calls(events, READ_TOOLS)
    notes = _note_paths(events)

    # Where the first note landed relative to the searching. The prompt says
    # "write as you go, not at the end"; a run that searches thirteen times and
    # then writes once leaves nothing behind if it dies on the twelfth.
    order = [e for e in events if e.get("event") == "tool_start"]
    first_note_at = next(
        (i for i, e in enumerate(order)
         if e.get("tool") in WRITE_TOOLS and _note_path(e)), None)
    searches_before_first_note = sum(
        1 for e in order[:first_note_at] if e.get("tool") in SEARCH_TOOLS
    ) if first_note_at is not None else len(searches)

    ungrounded = sum(1 for e in events
                     if e.get("event") == "tool_end"
                     and e.get("tool") in SEARCH_TOOLS
                     and _NO_SOURCES in str(e.get("output") or ""))

    queries = [str(_args(e).get("query") or "") for e in searches]

    return {
        "vocabulary": vocabulary(events),
        "searches": len(searches),
        "source_reads": len(reads),
        "reflections": len(_calls(events, REFLECT_TOOLS)),
        "ungrounded_searches": ungrounded,
        "notes_written": len(notes),
        "distinct_notes": len(set(notes)),
        "reports": len([n for n in set(notes) if REQUEST_NOTE not in n]),
        "saved_the_request": any(REQUEST_NOTE in n for n in notes),
        "searches_before_first_note": searches_before_first_note,
        "question_shaped_queries": sum(1 for q in queries
                                       if _looks_like_a_question(q)),
    }


def vocabulary(events: list) -> str:
    """Which research agent wrote this trace: `deep`, `classic`, or `unknown`."""
    tools = {e.get("tool") for e in events if e.get("event") == "tool_start"}
    if DEEP_SEARCH in tools or REFLECT_TOOLS & tools:
        return "deep"
    if CLASSIC_SEARCH in tools:
        return "classic"
    return "unknown"


@dataclass
class Check:
    """One property of a research run, with what it is protecting."""

    name: str
    ok: bool
    detail: str
    why: str

    def __str__(self) -> str:
        return f"[{'ok' if self.ok else 'FAIL'}] {self.name}: {self.detail}"


# The observed run, for anyone reading a threshold and wondering where it came
# from. Twelve searches over the budget, no source opened, one note at the end.
OBSERVED = {"searches": 13, "source_reads": 0, "notes_written": 1,
            "searches_before_first_note": 13, "question_shaped_queries": 0}

# Two budgets, because the two agents were given two different ones and a check
# that enforces the wrong one is measuring an opinion.
#
# The deep agent's is upstream's arithmetic, not ours: five searches per
# sub-agent times three parallel sub-agents is the ceiling a run following the
# prompt cannot exceed (agent/explore/session.py pins this).
DEEP_SEARCH_BUDGET = 15
# The classic agent had no number at all -- "stop when the answer stops moving"
# -- until one was written for it after a run spent 13 searches. Kept so its
# recorded traces still score against what it was told.
CLASSIC_SEARCH_BUDGET = 10
SEARCH_BUDGET = DEEP_SEARCH_BUDGET


def budget_for(events: list) -> int:
    """The search ceiling the run's own prompt set."""
    return (CLASSIC_SEARCH_BUDGET if vocabulary(events) == "classic"
            else DEEP_SEARCH_BUDGET)


def check(events: list, *, budget: Optional[int] = None) -> list:
    """Every check over one run. A regression is a check that flips to FAIL."""
    m = from_trace(events)
    if budget is None:
        budget = budget_for(events)
    out = []

    def add(name, ok, detail, why):
        out.append(Check(name, ok, detail, why))

    deep = m["vocabulary"] == "deep"

    if not deep:
        # Only the classic agent can fail this: its `web_search` returns a
        # summary and opening the page is a separate tool. `tavily_search`
        # returns the page, so on the deep agent the check has nothing to
        # catch -- it is satisfied by the shape of the tool, which is the whole
        # reason the tool changed.
        add("opened a source",
            m["source_reads"] > 0 or m["searches"] == 0,
            f"{m['source_reads']} read_url call(s) for {m['searches']} search(es)",
            "The prompt says to open the source with `read_url` before writing "
            "a version, a limit or a price down as fact. A run that never reads "
            "a page has taken every number from a summary of a summary.")

    add("stayed inside the search budget",
        m["searches"] <= budget,
        f"{m['searches']} search(es), budget {budget}",
        "A free tier is metered and a seventh confirming source costs what a "
        "first source on the next question costs.")

    add("wrote as it went",
        deep or m["searches_before_first_note"] <= max(3, budget // 2),
        f"{m['searches_before_first_note']} search(es) before the first note",
        "A run that dies holding everything in its head leaves nothing. The "
        "prompt asks for a file early and updates after.")

    # `reports`, not `distinct_notes`: research_request.md records the question
    # and answers nothing, so a run that saved it and stopped has left the next
    # reader exactly as uninformed as one that wrote nothing.
    add("left a deliverable",
        m["reports"] > 0,
        f"{m['reports']} report(s) under /research",
        "The closing message is not the deliverable and nobody reads it. No "
        "file means the run spent its quota talking to itself.")

    add("marked its ungrounded answers",
        deep or m["ungrounded_searches"] == 0 or m["source_reads"] > 0,
        f"{m['ungrounded_searches']} search(es) came back with no sources",
        "A grounded call that returns no citations answered from memory. The "
        "prompt forbids laundering that into a cited fact, so at least one "
        "page has to be opened before those claims are written down.")

    if deep:
        # The reference enforces a search -> think loop: reflect after every
        # search, before deciding to search again
        # (https://docs.langchain.com/oss/python/deepagents/deep-research).
        # A run that searches five times without once asking what it now knows
        # is the shape the loop exists to prevent.
        add("reflected between searches",
            m["searches"] == 0 or m["reflections"] >= m["searches"] - 1,
            f"{m['reflections']} think_tool call(s) for {m['searches']} "
            f"search(es)",
            "think_tool is a forced pause between retrieving and deciding to "
            "retrieve again. Skipping it is how a run spends its whole budget "
            "confirming what its second search already said.")

        add("saved the request",
            m["saved_the_request"],
            "wrote /research/research_request.md"
            if m["saved_the_request"] else "no research_request.md",
            "Step 2 of the workflow. It makes the directory self-describing: "
            "the next agent to open it can see what was asked, not only what "
            "was answered.")

    add("asked questions rather than keywords",
        m["searches"] == 0
        or m["question_shaped_queries"] >= (m["searches"] + 1) // 2,
        f"{m['question_shaped_queries']} of {m['searches']} query(ies) read as "
        f"questions",
        "`web_search` answers a question and only retrieves against keywords, "
        "so a query of quoted operators wastes the grounded half of the call.")

    return out


def failures(events: list, *, budget: Optional[int] = None) -> list:
    return [c for c in check(events, budget=budget) if not c.ok]


def report(events: list, *, budget: Optional[int] = None) -> str:
    return "\n".join(str(c) for c in check(events, budget=budget))
