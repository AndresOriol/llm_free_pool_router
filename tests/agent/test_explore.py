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


def test_the_explorer_is_offered_exactly_its_capabilities(tmp_path):
    """Read a named file, write one, edit one, plan, delegate, search, reflect,
    and see what it has written -- and nothing for browsing a repository it was
    never asked to explore."""
    model = _recorder()
    session.build_agent(tmp_path, model, pool=None, members=3).invoke(
        {"messages": [("user", "research something")]})

    assert _Recorder.seen[0] == ["write_todos", "read_file", "write_file",
                                 "edit_file", "task", "tavily_search",
                                 "think_tool", "research_status"]


def test_the_researcher_sub_agent_gets_the_same_surface(tmp_path):
    """It is built by the framework with its own copy of the filesystem tools,
    so without its own copy of the middleware it would be the one thing left in
    this system able to grep the project."""
    from deepagents import create_deep_agent

    model = _recorder()
    spec = session.researcher_subagent(
        list(research_tools.make_research_tools(pool=None).values()))
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


def test_research_status_lists_the_notes_with_their_titles(tmp_path):
    """The one listing this agent gets, and it is asked for rather than paid for
    on every call. A note written an hour ago is gone from the conversation
    after summarization; it is still on disk."""
    research = tmp_path / "research"
    research.mkdir()
    (research / "pricing.md").write_text("# What the vendors charge\n\nbody\n",
                                         encoding="utf-8")

    answer = notes.make_status_tool(research).invoke({})

    assert "/research/pricing.md" in answer
    assert "What the vendors charge" in answer


def test_research_status_is_bound_to_this_runs_directory(tmp_path):
    """Two investigations in one workdir must not see each other's listing."""
    (tmp_path / "research" / "old").mkdir(parents=True)
    (tmp_path / "research" / "old" / "prior.md").write_text(
        "# prior\n", encoding="utf-8")

    model = _recorder()
    session.build_agent(tmp_path, model, pool=None, members=3,
                        research_dir="research/new").invoke(
        {"messages": [("user", "research something")]})

    assert "research_status" in _Recorder.seen[0]
    assert "/research/new/" in _Recorder.system[0]
    assert "/research/old/" not in _Recorder.system[0]


def test_an_empty_research_directory_says_so(tmp_path):
    """Silence would read as "no directory". "Nothing is written yet" is the
    state in which a crash costs the whole run."""
    assert "empty" in notes.make_status_tool(tmp_path / "research").invoke({})


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


# --- the prompt the framework writes ----------------------------------------
# `create_deep_agent` appends its own sections describing the tool suite it
# installs. After the surface above, four of them describe an agent this is not,
# and one of those instructs the opposite of this agent's contract.


def test_the_framework_sections_about_tools_it_lacks_are_removed(tmp_path):
    model = _recorder()
    session.build_agent(tmp_path, model, pool=None, members=3).invoke(
        {"messages": [("user", "research something")]})
    text = _Recorder.system[0]

    for label, section in tools._framework_sections().items():
        assert section not in text, f"the {label} section survived"
    # The three tools the agent does not have must not be named as available.
    assert "## Filesystem Tools" not in text
    assert "## Execute Tool" not in text


def test_the_generated_list_of_subagents_survives_the_pruning(tmp_path):
    """It is appended after the section that is removed, and it is the only
    place the agent is told what it can delegate to."""
    model = _recorder()
    session.build_agent(tmp_path, model, pool=None, members=3).invoke(
        {"messages": [("user", "research something")]})

    assert "Available subagent types" in _Recorder.system[0]
    assert "research-agent" in _Recorder.system[0]


def test_the_agent_is_not_told_its_final_message_is_the_deliverable(tmp_path):
    """The todo middleware ends with "write your final answer in the message
    AFTER your last write_todos call". This agent's answer is a file, and a run
    that recites its report into a reply pays for the report twice."""
    model = _recorder()
    session.build_agent(tmp_path, model, pool=None, members=3).invoke(
        {"messages": [("user", "research something")]})

    assert "Finishing a task" not in _Recorder.system[0]
    assert "belongs in a file" in _Recorder.system[0]


def test_a_reworded_upstream_section_is_reported_rather_than_missed(caplog):
    """The removals match imported constants. If upstream rewords one the
    import still resolves, the removal silently does nothing, and this is the
    only thing that would say so."""
    tools._warned.clear()
    with caplog.at_level("WARNING"):
        assert tools._prune("nothing to remove here") == "nothing to remove here"
    assert "reworded upstream" in caplog.text
    tools._warned.clear()


# --- one directory per investigation ----------------------------------------


def test_a_named_research_directory_reaches_prompts_and_tools(tmp_path):
    model = _recorder()
    session.build_agent(tmp_path, model, pool=None, members=3,
                        research_dir="research/cv-spain").invoke(
        {"messages": [("user", "research something")]})

    text = _Recorder.system[0]
    assert "/research/cv-spain/final_report.md" in text
    # Nothing may still point at the default, or the agent writes to two places.
    assert "/research/final_report.md" not in text
    assert "/research/cv-spain/" in _Recorder.described["write_file"]
    assert (tmp_path / "research" / "cv-spain").is_dir()


def test_the_default_directory_is_left_exactly_as_written():
    """`retarget` is a substitution over assembled prose; on the default it must
    be the identity, or every prompt assertion is testing the rewriter."""
    text = "write it to /research/final_report.md"
    assert notes.retarget(text, "research") == text
    assert notes.retarget(text, "/research/") == text


def test_a_continued_investigation_sees_what_the_last_run_left(tmp_path):
    """The reason the directory is a parameter: point a second question at the
    first one's directory and its notes are there to read, extend and cite."""
    earlier = tmp_path / "research" / "cv-spain"
    earlier.mkdir(parents=True)
    (earlier / "retail.md").write_text("# Retail security in Spain\n",
                                       encoding="utf-8")

    answer = notes.make_status_tool(earlier, "research/cv-spain").invoke({})

    assert "/research/cv-spain/retail.md" in answer
    assert "Retail security in Spain" in answer


def test_a_delegated_exploration_can_be_given_its_own_directory(monkeypatch,
                                                                tmp_path):
    """A2A callers get the same seam: one directory per delegated question."""
    from agent.explore import a2a

    class _Fake:
        def invoke(self, state, config=None):
            path = tmp_path / "research" / "delegated" / "note.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# delegated\n", encoding="utf-8")
            return {"messages": [AIMessage("done")]}

    monkeypatch.setattr(session, "build_agent", lambda *a, **kw: _Fake())
    handler = a2a.make_handler(model=None, workdir=tmp_path, pool=None,
                               floor=128_000, members=1, recursion_limit=10,
                               research_dir="research/delegated")
    task = handler(_task())

    assert [a.parts[0].uri for a in task.artifacts] == [
        "research/delegated/note.md"]


# --- the sub-agents are held to the same surface ----------------------------


def test_the_general_purpose_subagent_is_declared_rather_than_defaulted():
    """The framework adds one when the caller declares none, and the default
    inherits the filesystem tools without our middleware -- so an agent with no
    `grep` could delegate to one that has, under no search budget either."""
    from langchain.agents.middleware import ToolCallLimitMiddleware

    spec = session.general_purpose_subagent([])

    assert spec["name"] == "general-purpose"
    kinds = {type(m) for m in spec["middleware"]}
    assert tools.ToolSurfaceMiddleware in kinds
    assert ToolCallLimitMiddleware in kinds
    assert "no shell" in spec["description"]


# --- step 6: the review at the end ------------------------------------------
# The prompt asks for it and `invoke_with_review` asks again when a run ends
# without it. Every skipped step in this agent's recorded history was one the
# prompt already asked for, and this is the step that decides whether the
# deliverable answers the question (agent/explore/session.py).


class _Agent:
    """A compiled agent, faked: writes the files each invocation is told to."""

    def __init__(self, research: Path, *writes):
        self.research, self.writes, self.calls = research, list(writes), []

    def invoke(self, state, config=None):
        self.calls.append(list(state.get("messages") or []))
        for name in (self.writes.pop(0) if self.writes else []):
            path = self.research / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# " + name, encoding="utf-8")
        return {"messages": [AIMessage("done")]}


def test_a_run_that_reviewed_is_left_alone(tmp_path):
    agent = _Agent(tmp_path, ["final_report.md", "review.md"])
    session.invoke_with_review(agent, {"messages": []}, {}, tmp_path)

    assert len(agent.calls) == 1


def test_a_run_that_skipped_the_review_is_asked_once(tmp_path):
    agent = _Agent(tmp_path, ["final_report.md"], ["review.md"])
    session.invoke_with_review(agent, {"messages": []}, {}, tmp_path)

    assert len(agent.calls) == 2
    # It continues the conversation rather than starting a new one: a review
    # that cannot see the run it is reviewing is a second run.
    assert session.REVIEW_FOLLOW_UP in agent.calls[1][-1].content
    assert (tmp_path / "review.md").exists()


def test_a_run_that_declines_twice_still_ends(tmp_path):
    """Advisory, never able to hold a session open. An unreviewed report is
    worth more than a run stuck arguing about one."""
    agent = _Agent(tmp_path, ["final_report.md"], [])
    session.invoke_with_review(agent, {"messages": []}, {}, tmp_path)

    assert len(agent.calls) == 2


def test_a_run_with_nothing_to_show_is_not_asked_to_review_it(tmp_path):
    """It wrote the request and the plan and no findings. There is nothing to
    read back, and the follow-up would spend a model call saying so."""
    agent = _Agent(tmp_path, ["research_request.md", "research_plan.md"])
    session.invoke_with_review(agent, {"messages": []}, {}, tmp_path)

    assert len(agent.calls) == 1


def test_an_earlier_investigations_review_does_not_count_as_this_ones(tmp_path):
    """Two questions can share a directory. A review from last week does not
    say anything about the report written today."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "review.md").write_text("# reviewed something else",
                                        encoding="utf-8")

    agent = _Agent(tmp_path, ["final_report-new.md"], ["review-new.md"])
    session.invoke_with_review(agent, {"messages": []}, {}, tmp_path)

    assert len(agent.calls) == 2


def test_the_workflow_asks_for_the_review_and_says_what_it_checks():
    text = _flat(session.orchestrator_prompt())

    assert "Reviewing your own output" in text
    assert "answered, partly answered" in session.REVIEW_FOLLOW_UP
    # The three things the review has to establish, in the prompt's own words.
    assert "was this one answered" in text.lower()
    assert "/research/review.md" in text
    assert "correct what you find, with edit_file" in text.lower()
