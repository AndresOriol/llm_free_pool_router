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

from agent.explore import prompt, research_tools, session
from agent.runtime.backend import RestrictedShellBackend


# -- What the model is told -------------------------------------------------

def test_prompt_leaves_no_placeholder_unreplaced():
    text = prompt.build(128_000, members=9)
    assert not re.findall(r"\{[a-z_]+\}", text)


def test_prompt_names_the_web_tools_and_the_deliverable():
    text = prompt.build(128_000, members=9,
                        extra_sections=[session.orchestrator_prompt()])
    # The deleted pair must not survive in prose: a prompt describing a tool
    # the agent does not have is a measured cause of failed calls.
    assert "web_search" not in text and "read_url" not in text
    # The whole point of this agent: the files are the output, not the reply.
    assert "/research/" in text
    assert "128,000 input tokens" in text


# -- Whether it can search at all ------------------------------------------

def test_a_run_with_no_tavily_account_is_refused_before_it_starts():
    class _EmptyPool:
        accounts = []

    try:
        session.check_pool(_EmptyPool())
    except session.NoSearchPool as exc:
        assert "TAVILY_API_KEY_1" in str(exc)
    else:
        raise AssertionError("a run with no way to search must refuse up front")


def test_one_tavily_account_works_and_is_warned_about():
    """It searches; it has nothing to fail over to. That is worth saying once,
    because the failure mode is every search dying at the monthly wall."""
    class _OnePool:
        accounts = [object()]

    assert session.check_pool(_OnePool()) == 1



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


# --- the explorer as an addressable agent -----------------------------------
# Its A2A handler (agent/explore/a2a.py). The protocol itself is checked in
# tests/agent/test_protocol.py; what is checked here is the one piece of logic
# that is the explorer's own -- deciding which files a delegated task produced.


class _FakeSession:
    """Stands in for a compiled explorer: writes notes, says something."""

    def __init__(self, root: Path, notes: dict, reply: str = "done"):
        self.root, self.notes, self.reply = root, notes, reply

    def invoke(self, state, config=None):
        for name, text in self.notes.items():
            path = self.root / session.RESEARCH_DIR / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return {"messages": [AIMessage(self.reply)]}


def _handler(monkeypatch, root: Path, notes: dict, reply: str = "done"):
    from agent.explore import a2a

    monkeypatch.setattr(session, "build_agent",
                        lambda *a, **kw: _FakeSession(root, notes, reply))
    return a2a.make_handler(model=None, workdir=root, pool=None,
                            floor=128_000, members=1, recursion_limit=10)


def _task(request: str = "what are the limits?"):
    from agent.protocol import Message, Task

    task = Task()
    task.history.append(Message.user(request, task_id=task.id))
    return task


def test_a_delegated_exploration_returns_its_notes_as_artifacts(monkeypatch,
                                                                tmp_path):
    handler = _handler(monkeypatch, tmp_path, {"limits.md": "# limits\n"})
    task = handler(_task())

    assert task.state == "completed"
    assert [a.name for a in task.artifacts] == ["limits.md"]
    assert task.artifacts[0].parts[0].uri == "research/limits.md"
    note = tmp_path / session.RESEARCH_DIR / "limits.md"
    assert task.artifacts[0].metadata["bytes"] == note.stat().st_size


def test_a_second_task_does_not_claim_the_first_ones_notes(monkeypatch,
                                                           tmp_path):
    """The failure this guards: a stale note read as the answer to a new
    question. Without the before-shot the second task reports both files."""
    handler = _handler(monkeypatch, tmp_path, {"first.md": "# one\n"})
    handler(_task("first question"))

    handler = _handler(monkeypatch, tmp_path, {"second.md": "# two\n"})
    second = handler(_task("second question"))

    assert [a.name for a in second.artifacts] == ["second.md"]


def test_a_rewritten_note_counts_as_this_tasks_work(monkeypatch, tmp_path):
    """Same path, new content -- the caller must be pointed at it again."""
    handler = _handler(monkeypatch, tmp_path, {"note.md": "# one\n"})
    handler(_task())

    handler = _handler(monkeypatch, tmp_path, {"note.md": "# rewritten, longer\n"})
    assert [a.name for a in handler(_task()).artifacts] == ["note.md"]


def test_an_empty_request_is_rejected_without_running_anything(monkeypatch,
                                                               tmp_path):
    from agent.protocol import Task

    handler = _handler(monkeypatch, tmp_path, {"never.md": "x"})
    task = handler(Task())

    assert task.state == "rejected"
    assert not (tmp_path / session.RESEARCH_DIR / "never.md").exists()


def test_the_closing_message_is_clipped(monkeypatch, tmp_path):
    """The explorer's sign-off is not the deliverable, and the caller pays for
    every token of it (docs/15-explorer.md#151-what-it-is-for)."""
    handler = _handler(monkeypatch, tmp_path, {"n.md": "x"}, reply="y" * 5_000)
    assert len(handler(_task()).status.message.text) <= 2_001


# --- the prompt and the checks have to agree --------------------------------
# `evals/research_trajectory.py` scores a run against rules the prompt states.
# If the two drift apart the checks stop measuring the agent's instructions and
# start measuring an opinion, which is the one thing they must not do.


def _flat(text):
    """The prompt with its line wrapping removed, so an assertion can quote a
    sentence the way it reads rather than the way it happens to be folded."""
    return " ".join(text.split())


def test_the_search_budget_the_checks_enforce_is_the_one_the_prompt_allows():
    """Not a number of ours. The ceiling a compliant run cannot exceed is the
    per-sub-agent budget times the parallel limit, both of them upstream's."""
    from evals.research_trajectory import SEARCH_BUDGET

    assert SEARCH_BUDGET == (session.MAX_SEARCHES_PER_SUBAGENT
                             * session.MAX_CONCURRENT_RESEARCH_UNITS)


def test_the_researcher_prompt_states_its_own_budget_and_stop_rule():
    text = _flat(session.researcher_subagent([])["system_prompt"])
    assert f"{session.MAX_SEARCHES_PER_SUBAGENT} search tool calls maximum" in text
    assert "You have 3+ relevant examples/sources for the question" in text
    assert "Your last 2 searches returned similar information" in text


def test_the_orchestrator_is_told_the_delegation_limits():
    text = _flat(session.orchestrator_prompt())
    assert (f"at most {session.MAX_CONCURRENT_RESEARCH_UNITS} parallel "
            f"sub-agents") in text
    assert (f"Stop after {session.MAX_RESEARCHER_ITERATIONS} delegation rounds"
            ) in text
    assert "ALWAYS use sub-agents for research, never conduct research yourself"         in text


def test_reading_the_page_is_structural_rather_than_instructed():
    """The old prompt asked the agent to open a source and it never did. The
    replacement removes the choice: the search tool returns the page."""
    tools = research_tools.make_research_tools(pool=None)
    assert set(tools) == {"tavily_search", "think_tool"}
    assert "full text of the pages" in tools["tavily_search"].description


def test_the_researcher_is_told_to_reflect_after_every_search():
    text = _flat(session.researcher_subagent([])["system_prompt"])
    assert "Use think_tool after each search" in text


def test_the_prompt_no_longer_teaches_keyword_search():
    """It used to hold `"gemini api google_search tool request format" beats
    "how to use gemini"` -- a keyword string offered as the good example, three
    sections below a tool description saying to ask a full question. Every one
    of a recorded run's thirteen queries was keyword-shaped."""
    from evals.research_trajectory import _looks_like_a_question

    text = _flat(session.researcher_subagent([])["system_prompt"])
    assert '"gemini api google_search tool request format"' not in text
    example = ("What fields does a SWE-bench instance carry and what is each "
               "for?")
    assert example in text and _looks_like_a_question(example)
