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

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from agent.explore import notes, prompt, research_tools, session, tools
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


# --- the tool surface -------------------------------------------------------
# What the model is *offered*. The jail is the boundary and is checked above;
# this is about what the schema invites, which is a different failure: a tool
# that is always refused, or a listing tool whose answer is already in the
# prompt, costs a request out of a daily budget to learn nothing
# (agent/explore/tools.py).


class _Recorder(GenericFakeChatModel):
    """A model that records the tools and the system message it was handed."""

    def bind_tools(self, tools, **kwargs):
        _Recorder.seen.append([getattr(t, "name", "") for t in tools])
        _Recorder.described.update({getattr(t, "name", ""): getattr(t, "description", "")
                                    for t in tools})
        return self

    def _generate(self, messages, *args, **kwargs):
        _Recorder.system.append(messages[0].text)
        return super()._generate(messages, *args, **kwargs)


def _recorder():
    _Recorder.seen, _Recorder.system, _Recorder.described = [], [], {}
    return _Recorder(messages=iter([AIMessage("done")]))


def test_the_explorer_is_offered_exactly_its_six_capabilities(tmp_path):
    """Read a named file, write one, edit one, plan, delegate, search, reflect
    -- and nothing for browsing a repository it was never asked to explore."""
    model = _recorder()
    session.build_agent(tmp_path, model, pool=None, members=3).invoke(
        {"messages": [("user", "research something")]})

    assert _Recorder.seen[0] == ["write_todos", "read_file", "write_file",
                                 "edit_file", "task", "tavily_search",
                                 "think_tool"]


def test_the_researcher_sub_agent_gets_the_same_surface(tmp_path):
    """It is built by the framework with its own copy of the filesystem tools,
    so without its own copy of the middleware it would be the one thing left in
    this system able to grep the project."""
    from deepagents import create_deep_agent

    model = _recorder()
    spec = session.researcher_subagent(
        list(research_tools.make_research_tools(pool=None).values()),
        tmp_path / session.RESEARCH_DIR)
    create_deep_agent(
        model=model, tools=spec["tools"], system_prompt=spec["system_prompt"],
        middleware=spec["middleware"],
        backend=RestrictedShellBackend(root_dir=str(tmp_path),
                                       allowed_programs=()),
    ).invoke({"messages": [("user", "research something")]})

    for excluded in tools.EXCLUDED:
        assert excluded not in _Recorder.seen[0]
    assert "tavily_search" in _Recorder.seen[0]


def test_the_rewritten_descriptions_reach_the_model(tmp_path):
    """Upstream's are a coding agent's: `read_file` explains itself in terms of
    codebase exploration and `write_todos` ends by saying the deliverable is the
    final message, which is the opposite of what this agent is told."""
    model = _recorder()
    session.build_agent(tmp_path, model, pool=None, members=3).invoke(
        {"messages": [("user", "research something")]})

    described = {name: _flat(text)
                 for name, text in _Recorder.described.items()}
    # Upstream's `read_file` sells pagination as the way to survive reading a
    # codebase; this one says where its two sources of paths are.
    assert "codebase exploration" not in described["read_file"]
    assert "There is no `ls`, no `glob` and no `grep`" in described["read_file"]
    # And upstream's `write_todos` closes by insisting the answer belongs in the
    # final message. Here the final message is clipped before its caller sees it.
    assert "the answer is the file under `/research/`" in described["write_todos"]
    assert "This replaces the whole file." in described["write_file"]


def test_tailoring_does_not_mutate_the_tools_it_was_given():
    """The tool objects are shared with the graph and with any other agent over
    the same backend; rewriting one in place would change a description the
    coding agent relies on."""
    from langchain_core.tools import StructuredTool

    def read_file(file_path: str) -> str:
        """Original description."""
        return ""

    original = StructuredTool.from_function(func=read_file, name="read_file",
                                            description="Original description.")
    tailored = tools._tailor([original])

    assert tailored[0].description == tools.READ_FILE
    assert original.description == "Original description."


# --- the research directory, in place of `ls` -------------------------------


def test_the_research_directory_is_listed_in_every_system_message(tmp_path):
    """The one listing this agent gets. It is rebuilt per call because it is the
    agent's own output: a note written an hour ago is gone from the conversation
    after summarization, and the directory is what still remembers it."""
    (tmp_path / session.RESEARCH_DIR).mkdir()
    (tmp_path / session.RESEARCH_DIR / "pricing.md").write_text(
        "# What the vendors charge\n\nbody\n", encoding="utf-8")

    model = _recorder()
    session.build_agent(tmp_path, model, pool=None, members=3).invoke(
        {"messages": [("user", "research something")]})

    text = _Recorder.system[0]
    assert "/research/pricing.md" in text
    assert "What the vendors charge" in text


def test_an_empty_research_directory_says_so(tmp_path):
    """Silence would read as "no directory". "Nothing is written yet" is the
    state in which a crash costs the whole run."""
    assert "empty" in notes.section(tmp_path / "research")


def test_the_listing_survives_a_note_it_cannot_read(tmp_path):
    """A listing that raises would end a run over a file permission."""
    research = tmp_path / "research"
    research.mkdir()
    (research / "unreadable.md").write_text("", encoding="utf-8")

    assert "/research/unreadable.md" in notes.listing(research)


def test_the_project_tree_is_not_in_the_prompt(tmp_path):
    """The coding agent opens with one because it has to find its way around a
    repository. This agent is given its question, and the paths worth reading
    belong in the brief -- so a tree here is an invitation to the one thing the
    surface no longer supports."""
    (tmp_path / "some_package").mkdir()

    model = _recorder()
    session.build_agent(tmp_path, model, pool=None, members=3).invoke(
        {"messages": [("user", "research something")]})

    assert "some_package" not in _Recorder.system[0]


def test_the_prompt_names_the_surface_and_the_missing_tools(tmp_path):
    """A prompt that describes a tool the agent does not have is a measured
    cause of failed calls; so is one that stays silent about a gap."""
    text = _flat(prompt.build(128_000, members=9,
                              extra_sections=[session.orchestrator_prompt()]))

    assert "There is no shell, no `ls`, no `glob` and no `grep`" in text
    assert "`ls /research`" not in text
