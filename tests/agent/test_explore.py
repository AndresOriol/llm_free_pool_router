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

from agent.explore import agent as explore
from deepagents.backends.filesystem import FilesystemBackend
from llm_router.tavily_router import NoSearchPool, TavilyPoolRouter


def _flat(text):
    """Text with its line wrapping removed, so an assertion can quote a
    sentence the way it reads rather than the way it happens to be folded."""
    return " ".join(text.split())


def _values(research_dir="research"):
    return explore.template_values(128_000, members=9, research_dir=research_dir)


def _orchestrator(research_dir="research"):
    return explore.prompt(explore.ORCHESTRATOR_PROMPTS, _values(research_dir))


def _researcher():
    return explore.prompt(explore.RESEARCHER_PROMPTS, _values())


def _tools(workdir, research_dir="research"):
    """The three tools this agent adds, by name."""
    described = explore.descriptions(_values(research_dir))
    return {t.name: t for t in explore.research_tools(
        Path(workdir), research_dir, described)}


def _subagents(workdir):
    values = _values()
    described = explore.descriptions(values)
    # `build_agent` does this; a test reaching for the specs directly has to do
    # it too, or the profile the specs rely on is not registered.
    explore.register_surface(values)
    return explore.subagents(list(_tools(workdir).values()), values, described)


# -- What the model is told -------------------------------------------------

def test_no_text_file_leaves_a_placeholder_unfilled():
    for text in [_orchestrator(), _researcher(),
                 *explore.descriptions(_values()).values()]:
        assert not re.findall(r"\{[a-z_]+\}", text)


def test_prompt_names_the_web_tools_and_the_deliverable():
    text = _orchestrator()
    # The deleted pair must not survive in prose: a prompt describing a tool
    # the agent does not have is a measured cause of failed calls.
    assert "web_search" not in text and "read_url" not in text
    # The whole point of this agent: the files are the output, not the reply.
    assert "/research/" in text
    assert "128,000 input tokens" in text


def test_every_tool_description_is_a_file_rather_than_a_string_in_code():
    """The point of the directory: what the agent is told is edited as text."""
    described = explore.descriptions(_values())

    assert {"read_file", "write_file", "edit_file", "write_todos", "task",
            "tavily_search", "think_tool", "research_status"} == set(described)
    assert all(text.strip() for text in described.values())


# -- Whether it can search at all ------------------------------------------

def test_a_run_with_no_tavily_account_is_refused_before_it_starts():
    """`connect()` asks for the pool at start-up precisely so this lands here
    and not on the first search, an hour of quota later."""
    pool = TavilyPoolRouter(accounts=[])

    try:
        pool.check()
    except NoSearchPool as exc:
        assert "TAVILY_API_KEY_1" in str(exc)
    else:
        raise AssertionError("a run with no way to search must refuse up front")


def test_one_tavily_account_works_and_is_warned_about():
    """It searches; it has nothing to fail over to. That is worth saying once,
    because the failure mode is every search dying at the monthly wall."""
    assert TavilyPoolRouter(accounts=[object()]).check() == 1


# -- What it is allowed to do ------------------------------------------------

def test_the_explorer_has_nothing_to_run_programs_with():
    """Not a shell that refuses every command: no shell. The framework offers
    `execute` only on a backend that can execute, so it is never offered."""
    from deepagents.backends.protocol import SandboxBackendProtocol

    backend = FilesystemBackend(virtual_mode=True, root_dir=tempfile.mkdtemp())
    assert not isinstance(backend, SandboxBackendProtocol)
    assert not hasattr(backend, "execute")


def test_the_explorer_still_reads_and_writes_files():
    # The handoff to the coding agent is the filesystem, so this is the one
    # capability the explorer cannot lose.
    root = Path(tempfile.mkdtemp())
    backend = FilesystemBackend(virtual_mode=True, root_dir=str(root))
    backend.write("/research/notes.md", "# what I found\n")
    assert (root / "research" / "notes.md").read_text() == "# what I found\n"
    assert "what I found" in (backend.read("/research/notes.md")
                              .file_data or {}).get("content", "")


# --- what the command reports ------------------------------------------------
# The explorer is reached by running it (docs/agents/delegation.md), so its stdout
# is what a caller -- a person, or the coding agent that ran the command --
# reads back. The one piece of logic that is the explorer's own lives here:
# deciding which notes *this* run wrote.

from agent.explore import __main__ as explore_cli  # noqa: E402


def _report(capsys, workdir, notes, before=None, reply="done"):
    """`_summary` over a research directory holding `notes`. Returns stdout."""
    directory = workdir / explore.RESEARCH_DIR
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in notes.items():
        (directory / name).write_text(text, encoding="utf-8")
    explore_cli._summary({"messages": [AIMessage(reply)]}, None, workdir, before)
    return capsys.readouterr().out


def test_the_final_message_is_the_output_of_the_command(capsys, tmp_path):
    """A caller that ran this reads stdout and nothing else, so the answer has
    to be in it -- first, and whole."""
    out = _report(capsys, tmp_path, {"limits.md": "# limits\n"},
                  reply="Cerebras allows 14,400 requests a day.")

    assert "Cerebras allows 14,400 requests a day." in out
    assert out.index("Cerebras") < out.index("=== DONE")


def test_a_long_final_message_is_not_clipped(capsys, tmp_path):
    """It was clipped to 2,000 characters when nobody read it. Now it is the
    answer a caller gets back, and half an answer is worse than none."""
    out = _report(capsys, tmp_path, {"n.md": "x"}, reply="y" * 5_000)
    assert "y" * 5_000 in out


def test_the_notes_this_run_wrote_are_named(capsys, tmp_path):
    out = _report(capsys, tmp_path, {"limits.md": "# limits\n"})

    assert "notes written by this run: 1" in out
    assert "research/limits.md" in out


def test_a_second_run_does_not_claim_the_first_ones_notes(capsys, tmp_path):
    """The failure this guards: a stale note read as the answer to a new
    question. Without the before-shot the second run reports both files."""
    _report(capsys, tmp_path, {"first.md": "# one\n"})
    before = explore_cli._notes(tmp_path)

    out = _report(capsys, tmp_path, {"second.md": "# two\n"}, before)

    assert "notes written by this run: 1" in out
    assert "research/second.md" in out
    assert "not this run's findings" in out and "research/first.md" in out


def test_a_rewritten_note_counts_as_this_runs_work(capsys, tmp_path):
    """Same path, new content -- the caller must be pointed at it again."""
    _report(capsys, tmp_path, {"note.md": "# one\n"})
    before = explore_cli._notes(tmp_path)

    out = _report(capsys, tmp_path, {"note.md": "# rewritten, longer\n"}, before)

    assert "notes written by this run: 1" in out
    assert "research/note.md" in out


def test_a_run_that_wrote_nothing_says_so_loudly(capsys, tmp_path):
    """Silence here reads as success and is not: the run spent quota and left
    nothing behind (docs/agents/explore.md#what-it-is-for)."""
    out = _report(capsys, tmp_path, {}, reply="I researched it thoroughly.")

    assert "notes written by this run: none" in out


# --- the prompt and the checks have to agree --------------------------------
# `evals/research_trajectory.py` scores a run against rules the prompt states.
# If the two drift apart the checks stop measuring the agent's instructions and
# start measuring an opinion, which is the one thing they must not do.


def test_the_search_budget_the_checks_enforce_is_the_one_the_prompt_allows():
    """Not a number of ours. The ceiling a compliant run cannot exceed is the
    per-sub-agent budget times the parallel limit, both of them upstream's."""
    from evals.research_trajectory import SEARCH_BUDGET

    assert SEARCH_BUDGET == (explore.MAX_SEARCHES_PER_SUBAGENT
                             * explore.MAX_CONCURRENT_RESEARCH_UNITS)


def test_the_researcher_prompt_states_its_own_budget_and_stop_rule():
    text = _flat(_researcher())
    assert f"{explore.MAX_SEARCHES_PER_SUBAGENT} search tool calls maximum" in text
    assert "You have 3+ relevant examples/sources for the question" in text
    assert "Your last 2 searches returned similar information" in text


def test_the_orchestrator_is_told_the_delegation_limits():
    text = _flat(_orchestrator())
    assert (f"at most {explore.MAX_CONCURRENT_RESEARCH_UNITS} parallel "
            f"sub-agents") in text
    assert (f"Stop after {explore.MAX_RESEARCHER_ITERATIONS} delegation rounds"
            ) in text
    assert "ALWAYS use sub-agents for research, never conduct research yourself" \
        in text


def test_the_orchestrator_is_told_todays_date():
    from datetime import date

    assert f"Today's research date is {date.today().isoformat()}" in _orchestrator()


def test_reading_the_page_is_structural_rather_than_instructed(tmp_path):
    """The old prompt asked the agent to open a source and it never did. The
    replacement removes the choice: the search tool returns the page."""
    made = _tools(tmp_path)
    assert set(made) == {"tavily_search", "think_tool", "research_status"}
    assert "full text of the pages" in made["tavily_search"].description


def test_the_researcher_is_told_to_reflect_after_every_search():
    assert "Use think_tool after each search" in _flat(_researcher())


def test_the_prompt_no_longer_teaches_keyword_search():
    """It used to hold `"gemini api google_search tool request format" beats
    "how to use gemini"` -- a keyword string offered as the good example, three
    sections below a tool description saying to ask a full question. Every one
    of a recorded run's thirteen queries was keyword-shaped."""
    from evals.research_trajectory import _looks_like_a_question

    text = _flat(_researcher())
    assert '"gemini api google_search tool request format"' not in text
    example = ("What fields does a SWE-bench instance carry and what is each "
               "for?")
    assert example in text and _looks_like_a_question(example)


# --- the tool surface -------------------------------------------------------
# What the model is *offered*. The jail is the boundary and is checked above;
# this is about what the schema invites, which is a different failure: a tool
# that is always refused, or a listing tool whose answer is already in the
# prompt, costs a request out of a daily budget to learn nothing.


class _Recorder(GenericFakeChatModel):
    """A model that records the tools and the system message it was handed.

    It impersonates the pool well enough for a harness profile to resolve --
    `routerchatmodel` as the provider (deepagents reads it off the class name)
    and `for_agent` to carry the identifier. Without that the profile
    registered by `register_surface` would match nothing and these tests would
    assert the framework's default surface while looking like they passed.
    """

    model_name: str = ""

    def _get_ls_params(self, **kwargs):
        params = super()._get_ls_params(**kwargs)
        params["ls_provider"] = "routerchatmodel"
        return params

    def for_agent(self, name):
        return self.model_copy(update={"model_name": name})

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


def _invoke(tmp_path, **kwargs):
    """Build the agent over the recorder and run it one step."""
    explore.build_agent(tmp_path, _recorder(), members=3,
                        **kwargs).invoke(
        {"messages": [("user", "research something")]})


def test_the_explorer_is_offered_exactly_its_capabilities(tmp_path):
    """Read a named file, write one, edit one, plan, delegate, search, reflect,
    and see what it has written -- and nothing for browsing a repository it was
    never asked to explore."""
    _invoke(tmp_path)

    assert _Recorder.seen[0] == ["write_todos", "read_file", "write_file",
                                 "edit_file", "task", "tavily_search",
                                 "think_tool", "research_status"]


def test_the_researcher_sub_agent_gets_the_same_surface(tmp_path):
    """It is built by the framework with its own copy of the filesystem tools,
    so without its own copy of the middleware it would be the one thing left in
    this system able to grep the project."""
    from deepagents import create_deep_agent

    spec = _subagents(tmp_path)[0]
    create_deep_agent(
        # Keyed like `build_agent` keys it: the exclusions live in the harness
        # profile now, and an unkeyed model resolves none of them.
        model=_recorder().for_agent(explore.AGENT), tools=spec["tools"],
        system_prompt=spec["system_prompt"], middleware=spec["middleware"],
        backend=FilesystemBackend(virtual_mode=True, root_dir=str(tmp_path)),
    ).invoke({"messages": [("user", "research something")]})

    for excluded in (*explore.EXCLUDED_TOOLS, "execute"):
        assert excluded not in _Recorder.seen[0]
    assert "tavily_search" in _Recorder.seen[0]


def test_every_subagent_is_held_to_the_same_surface(tmp_path):
    """The framework adds a `general-purpose` sub-agent when the caller declares
    none, and its default inherits the filesystem tools without our middleware --
    so an agent with no `grep` could delegate to one that has, under no search
    budget either. Both are declared, and both get this list."""
    from langchain.agents.middleware import ToolCallLimitMiddleware

    specs = _subagents(tmp_path)

    assert [s["name"] for s in specs] == ["research-agent", "general-purpose"]
    for spec in specs:
        assert {type(m) for m in spec["middleware"]} == {
            explore.FrameworkSurface, ToolCallLimitMiddleware}


def test_the_rewritten_descriptions_reach_the_model(tmp_path):
    """Upstream's are a coding agent's: `read_file` explains itself in terms of
    codebase exploration and `write_todos` ends by saying the deliverable is the
    final message, which is the opposite of what this agent is told."""
    _invoke(tmp_path)

    described = {name: _flat(text)
                 for name, text in _Recorder.described.items()}
    # Upstream's `read_file` sells pagination as the way to survive reading a
    # codebase; this one says where its two sources of paths are.
    assert "codebase exploration" not in described["read_file"]
    assert "There is no `ls`, no `glob` and no `grep`" in described["read_file"]
    # And upstream's `write_todos` closes by insisting the answer belongs in the
    # final message. Here the final message only points at the files.
    assert "the answer is the file under `/research/`" in described["write_todos"]
    assert "This replaces the whole file." in described["write_file"]


def test_describing_a_tool_does_not_mutate_the_one_it_was_given():
    """The tool objects are shared with the graph and with any other agent over
    the same backend; rewriting one in place would change a description the
    coding agent relies on.

    Only `PROFILE_BLIND` still goes through here -- the rest of the surface is
    the harness profile, and deepagents copies there for the same reason."""
    from langchain_core.tools import StructuredTool

    def write_todos(todos: str) -> str:
        """Original description."""
        return ""

    class _Request:
        tools, system_message = [], None

        def override(self, **changes):
            return changes

    original = StructuredTool.from_function(func=write_todos, name="write_todos",
                                            description="Original description.")
    request = _Request()
    request.tools = [original]
    applied = explore.FrameworkSurface({"write_todos": "Ours."})._apply(request)

    assert applied["tools"][0].description == "Ours."
    assert original.description == "Original description."


def test_the_profile_key_matches_the_live_model():
    """The whole surface hangs off this key resolving.

    `register_surface` registers under `routerchatmodel:explore`; deepagents
    derives the provider half from the model's *class name* and the identifier
    half from `model_name`. Rename `RouterChatModel`, or drop `for_agent`, and
    every exclusion and description in the profile stops applying with nothing
    raised -- the agent would quietly get `grep` back and upstream's wording.
    """
    from deepagents._models import get_model_identifier, get_model_provider

    from agent.utils.chat_model import RouterChatModel
    from agent.utils.pool import keyed

    model = keyed(RouterChatModel(router=None), explore.AGENT)

    assert get_model_provider(model) == "routerchatmodel"
    assert get_model_identifier(model) == explore.AGENT
    assert explore.PROFILE_KEY == (f"{get_model_provider(model)}:"
                                   f"{get_model_identifier(model)}")


# --- the research directory, in place of `ls` -------------------------------


def _status(workdir, research_dir="research"):
    return _tools(workdir, research_dir)["research_status"].invoke({})


def test_research_status_lists_what_has_been_written(tmp_path):
    """The one listing this agent gets, and it is asked for rather than paid for
    on every call. A note written an hour ago is gone from the conversation
    after summarization; it is still on disk."""
    research = tmp_path / "research"
    research.mkdir()
    (research / "pricing.md").write_text("body", encoding="utf-8")

    assert "/research/pricing.md" in _status(tmp_path)


def test_research_status_is_bound_to_this_runs_directory(tmp_path):
    """Two investigations in one workdir must not see each other's listing."""
    (tmp_path / "research" / "old").mkdir(parents=True)
    (tmp_path / "research" / "old" / "prior.md").write_text(
        "# prior\n", encoding="utf-8")

    _invoke(tmp_path, research_dir="research/new")

    assert "research_status" in _Recorder.seen[0]
    assert "/research/new/" in _Recorder.system[0]
    assert "/research/old/" not in _Recorder.system[0]
    assert "empty" in _status(tmp_path, "research/new")


def test_an_empty_research_directory_says_so(tmp_path):
    """Silence would read as "no directory". "Nothing is written yet" is the
    state in which a crash costs the whole run."""
    assert "empty" in _status(tmp_path)


def test_an_empty_note_is_still_listed(tmp_path):
    """A file with nothing in it is exactly the one worth seeing in a listing."""
    research = tmp_path / "research"
    research.mkdir()
    (research / "started.md").write_text("", encoding="utf-8")

    assert "/research/started.md" in _status(tmp_path)


def test_the_project_tree_is_not_in_the_prompt(tmp_path):
    """The coding agent opens with one because it has to find its way around a
    repository. This agent is given its question, and the paths worth reading
    belong in the brief -- so a tree here is an invitation to the one thing the
    surface no longer supports."""
    (tmp_path / "some_package").mkdir()

    _invoke(tmp_path)

    assert "some_package" not in _Recorder.system[0]


def test_the_prompt_names_the_surface_and_the_missing_tools():
    """A prompt that describes a tool the agent does not have is a measured
    cause of failed calls; so is one that stays silent about a gap."""
    text = _flat(_orchestrator())

    assert "There is no shell, no `ls`, no `glob` and no `grep`" in text
    assert "`ls /research`" not in text


# --- the prompt the framework writes ----------------------------------------
# `create_deep_agent` appends its own sections describing the tool suite it
# installs. After the surface above, four of them describe an agent this is not,
# and one of those instructs the opposite of this agent's contract.


def test_the_framework_sections_about_tools_it_lacks_are_removed(tmp_path):
    _invoke(tmp_path)
    text = _Recorder.system[0]

    for section in explore.PRUNED_SECTIONS:
        assert section not in text, "a framework section survived the pruning"
    # The three tools the agent does not have must not be named as available.
    assert "## Filesystem Tools" not in text
    assert "## Execute Tool" not in text


def test_the_generated_list_of_subagents_survives_the_pruning(tmp_path):
    """It is appended after the section that is removed, and it is the only
    place the agent is told what it can delegate to."""
    _invoke(tmp_path)

    assert "Available subagent types" in _Recorder.system[0]
    assert "research-agent" in _Recorder.system[0]


def test_the_agent_is_not_told_its_final_message_is_the_deliverable(tmp_path):
    """The todo middleware ends with "write your final answer in the message
    AFTER your last write_todos call". This agent's answer is a file, and a run
    that recites its report into a reply pays for the report twice."""
    _invoke(tmp_path)

    assert "Finishing a task" not in _Recorder.system[0]
    assert "belongs in a file" in _Recorder.system[0]


# --- one directory per investigation ----------------------------------------


def test_a_named_research_directory_reaches_prompts_and_tools(tmp_path):
    _invoke(tmp_path, research_dir="research/cv-spain")

    text = _Recorder.system[0]
    assert "`/research/cv-spain/` is a wiki" in text
    # Nothing may still point at the default, or the agent writes to two places;
    # and nothing may be substituted twice.
    assert "`/research/` is a wiki" not in text
    assert "cv-spain/cv-spain" not in text
    assert "/research/cv-spain/" in _Recorder.described["write_file"]
    assert (tmp_path / "research" / "cv-spain").is_dir()


def test_a_continued_investigation_sees_what_the_last_run_left(tmp_path):
    """The reason the directory is a parameter: point a second question at the
    first one's directory and its notes are there to read, extend and cite."""
    earlier = tmp_path / "research" / "cv-spain"
    earlier.mkdir(parents=True)
    (earlier / "retail.md").write_text("body", encoding="utf-8")

    assert "/research/cv-spain/retail.md" in _status(tmp_path, "research/cv-spain")


# --- steps 6 and 7: the review and the log, which only the prompt asks for ---


def test_the_prompt_makes_the_review_and_the_log_the_last_things_it_does():
    """There is no code behind this. The system prompt says a run is not
    finished until the request has been read back and the log entry written, and
    the workflow says what the review has to establish -- so these sentences
    are the whole mechanism (docs/agents/explore.md#the-review-at-the-end)."""
    text = _flat(_orchestrator())

    assert "The last things you do are the review and the log entry" in text
    assert "If you are about to write a final message and `log.md` has no "\
           "entry for this run, you are not finished" in text
    assert "Reviewing your own work" in text
    assert "was this one answered" in text.lower()
    assert "correct what you find with edit_file" in text.lower()


def test_the_prompt_describes_a_wiki_the_next_run_can_continue():
    """A research directory is expanded by later runs, so the prompt names the
    files a run starts from and the ones it must leave current."""
    text = _flat(_orchestrator())

    for name in ("index.md", "overview.md", "open-questions.md", "log.md"):
        assert f"`{name}`" in text
    assert "answered from `open-questions.md`" in text
