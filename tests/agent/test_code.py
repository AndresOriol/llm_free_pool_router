"""The coding agent's configuration, checked without the network.

Everything here is about what the agent is *told* and what it is *allowed*,
which is the part of the configuration that can be wrong silently. Whether the
agent then does good work is the scenarios' question, not this file's.
"""

import re
from pathlib import Path

from langchain_core.messages import HumanMessage, ToolMessage

from agent.code import agent as code


def _prompt(floor=128_000, members=0, programs=()):
    return code.system_prompt(code.template_values(floor, members, programs))


def test_prompt_leaves_no_placeholder_unreplaced():
    # A missed placeholder ships literal `{braces}` to the model, which reads as
    # an instruction it cannot follow rather than as a bug.
    text = _prompt(members=14)
    assert not re.findall(r"\{[a-z_]+\}", text)


def test_prompt_states_the_floor_not_a_model_name():
    text = _prompt(members=14)
    assert "128,000 input tokens" in text
    assert "pool of models" in text


def test_prompt_roots_the_file_tools_at_the_workspace():
    # The backend runs virtual_mode=True, so telling the model to build host
    # paths for the *file tools* -- which is what dcode's prompt does -- would
    # fail every one of those calls. `execute` is the host shell and is told so
    # separately, because saying `/` is all it can reach would be false.
    text = _prompt()
    assert "rooted at `/`" in text
    assert "absolute *within the project*" in text
    assert "`execute` runs in the project directory" in text


def test_prompt_forbids_editing_the_thing_that_contradicts_the_task(monkeypatch):
    """The one behaviour the guard exists to stop.

    A run in the first full-set batch was told to write a field onto objects the
    documentation says are never modified. It mutated them, replaced the test
    guarding the invariant with one asserting the opposite, and deleted the
    guarantee from the page.
    """
    monkeypatch.delenv("AGENT_INVARIANT_GUARD", raising=False)
    text = _prompt()
    assert "## Contradicted Requests" in text
    assert "Never edit a test or a document so that it stops contradicting you" in text


def test_the_guard_overrides_the_ambiguity_guidance_explicitly(monkeypatch):
    """Both are in the prompt and they point opposite ways on this case.

    A headless run is told to pick a reading and proceed, which is right for
    ambiguity and is the wrong instruction in front of a stated guarantee. The
    section has to say which one wins, or it is one more thing to weigh.
    """
    monkeypatch.delenv("AGENT_INVARIANT_GUARD", raising=False)
    text = _prompt()
    assert 'overrides "make reasonable assumptions and proceed"' in text
    assert "make reasonable assumptions and proceed" in text.split(
        "## Contradicted Requests")[0]


def test_the_guard_is_a_configuration_that_can_be_turned_off(monkeypatch):
    """Per CLAUDE.md a prompt change is a branch measured against a baseline.

    `AGENT_INVARIANT_GUARD=0` is the other arm, the same arrangement AGENT_PEERS
    uses -- and the placeholder still has to be gone, or turning it off ships
    literal braces to the model.
    """
    monkeypatch.setenv("AGENT_INVARIANT_GUARD", "0")
    text = _prompt()
    assert "Contradicted Requests" not in text
    assert not re.findall(r"\{[a-z_]+\}", text)


def test_the_guard_distinguishes_explicit_doc_and_spec_updates(monkeypatch):
    """Explicit requests to update docs or migrate specifications are valid work.

    The invariant guard forbids unrequested edits that hide contradictions, but
    must explicitly allow tasks that ask to update documentation or migrate code
    and specs to a new data model version.
    """
    monkeypatch.delenv("AGENT_INVARIANT_GUARD", raising=False)
    text = _prompt()
    assert "Explicit Updates and Specification Migrations:" in text
    assert "explicitly asks to update documentation or migrate code" in text


def test_the_project_notes_are_an_exception_to_the_documentation_rule(monkeypatch):
    """R7's deliverable, against a rule that forbade exactly it.

    `## Documentation` says not to create summary markdown files describing work
    just done -- right for a one-shot task, precisely wrong for a standing
    maintainer whose deliverable is the account. 7 of 23 runs that solved their
    task wrote one; the agent was obeying.
    """
    monkeypatch.delenv("AGENT_WRITE_ACCOUNT", raising=False)
    flat = " ".join(_prompt().split())

    assert "Do not create summary markdown files" in flat
    assert "feedback file is the exception" in flat
    assert "NOTES.md" in flat


def test_the_account_rule_asks_for_what_happened_not_what_was_hoped(monkeypatch):
    """C4: a confidently wrong rationale reads exactly like a correct one.

    `wrote_account` counts lines and cannot see this, so the prompt has to.
    """
    monkeypatch.delenv("AGENT_WRITE_ACCOUNT", raising=False)
    flat = " ".join(_prompt().split())

    assert "Write what happened, not what was hoped for" in flat
    assert "anything you left undone" in flat


def test_the_account_rule_is_a_configuration_that_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("AGENT_WRITE_ACCOUNT", "0")
    text = _prompt()

    assert "feedback file is the exception" not in text
    assert "Do not create summary markdown files" in text
    assert not re.findall(r"\{[a-z_]+\}", text)


def test_the_prompt_does_not_claim_the_shell_is_confined(tmp_path):
    """`execute` is the host shell. A prompt that says otherwise is a promise
    the harness cannot keep, and the model plans around promises."""
    text = code.system_prompt(code.template_values())

    assert "`/` is all you can reach" not in text
    assert "Nothing else runs at all" not in text
    assert "Stay inside the project" in text


def test_the_prompt_names_the_shell_execute_runs(monkeypatch):
    """Left unnamed, the model assumes POSIX on a Windows host and wraps its
    commands in `python -c` subprocess calls to get around the guess."""
    monkeypatch.setattr("agent.utils.prompts.os.name", "nt")
    monkeypatch.setenv("COMSPEC", r"C:\Windows\system32\cmd.exe")
    assert "hands each command to `cmd.exe` (Windows)" in _prompt()

    monkeypatch.setattr("agent.utils.prompts.os.name", "posix")
    assert "hands each command to `/bin/sh`" in _prompt()

def test_tree_skips_caches_and_dotfiles(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("x = 1")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "junk.pyc").write_text("junk")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "lib.py").write_text("junk")

    listing = code.tree(tmp_path)
    assert "/pkg/mod.py" in listing
    assert "__pycache__" not in listing
    assert ".venv" not in listing


def test_tree_truncates_rather_than_flooding_context(tmp_path: Path):
    for i in range(50):
        (tmp_path / f"f{i}.py").write_text("x = 1")
    listing = code.tree(tmp_path, max_entries=10)
    assert "listing stopped at 10 entries" in listing
    assert len(listing.splitlines()) == 11


# --- skills -------------------------------------------------------------------
#
# A skill costs one line of prompt -- its name and description -- until the
# agent decides it applies. Which makes two things load-bearing: the
# description has to say when it applies, and the body has to be somewhere
# `read_file` can actually reach.


def _rendered(peers=("explore",), workdir=Path(".")):
    return code.render_skills(list(peers), workdir)


def test_no_peers_renders_nothing_to_mount():
    """`AGENT_PEERS=` is the baseline arm. It must not get a skill explaining
    how to delegate to agents it cannot reach."""
    assert code.render_skills([], Path(".")) is None


def test_a_rendered_skill_parses_and_carries_a_description():
    """The middleware parses the frontmatter as YAML and silently drops a skill
    it cannot read. The description is prose with colons, backticks and `*` in
    it, so the template quotes it as a block scalar -- read it back through the
    middleware's own loader, not a hand-rolled split."""
    from deepagents.backends.filesystem import FilesystemBackend
    from deepagents.middleware.skills import _list_skills

    rendered = _rendered()  # held: the directory dies with this object
    listed = _list_skills(FilesystemBackend(root_dir=rendered.name,
                                            virtual_mode=True), "/")
    assert [skill["name"] for skill in listed] == ["delegate"]
    assert "python -m agent.*" in listed[0]["description"]

    body = (Path(rendered.name) / "delegate" / "SKILL.md").read_text(encoding="utf-8")
    assert not re.findall(r"\{[a-z_]+\}", body), "an unfilled placeholder"


def test_the_roster_is_rendered_per_run_not_committed():
    """Who can be reached is probed at startup, so it cannot be a committed
    file -- a skill that advertises an agent this run cannot run costs a step
    to disprove."""
    committed = (code.SKILLS_DIR / "delegate" / "SKILL.md").read_text(encoding="utf-8")
    assert "{roster}" in committed and "{description}" in committed

    rendered = _rendered(["code"])
    body = (Path(rendered.name) / "delegate" / "SKILL.md").read_text(encoding="utf-8")
    assert "python -m agent.code" in body
    assert "agent.explore" not in body, "it was not offered to this run"


def test_a_skill_is_readable_from_a_workspace_that_is_not_this_repository(tmp_path):
    """The whole reason the directory is mounted rather than named by an
    absolute path: the middleware prints `/skills/...`, and a run jailed to
    `tmp_path` has to be able to read it with its own `read_file`."""
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    class _ReadsTheSkill(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    path = code.SKILLS_ROOT + "delegate/SKILL.md"
    model = _ReadsTheSkill(messages=iter([
        AIMessage(content="", tool_calls=[
            {"name": "read_file", "args": {"file_path": path}, "id": "c1"}]),
        AIMessage(content="read it"),
    ]))
    built = code.build_agent(tmp_path, model, peers=["explore"])
    messages = built.invoke({"messages": [HumanMessage("hi")]},
                            {"recursion_limit": 10})["messages"]

    read = next(m for m in messages if isinstance(m, ToolMessage))
    assert "Delegating work to another agent" in str(read.content), read.content


def test_skills_cost_no_tool():
    """Progressive disclosure is a prompt section and `read_file`. If a skill
    ever arrived as a tool it would be charged on every step of every run
    ([6.4](../../docs/06-agent.md#64-why-it-is-shaped-this-way))."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    built = code.build_agent(".", FakeListChatModel(responses=["x"]),
                             peers=["explore"])
    node = built.nodes["tools"]
    names = set(getattr(node, "bound", node).tools_by_name)
    assert names == {"ls", "read_file", "write_file", "edit_file", "glob",
                     "grep", "execute", "write_todos", "task"}


# --- The project's own memory file ---------------------------------------------


def _system_prompt_of_a_run(workdir) -> str:
    """The system message the model is actually sent, for a run over `workdir`.

    The memory file is loaded by middleware `before_agent`, not by
    `build_agent`, so nothing short of starting the graph proves it arrived.
    """
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    seen = []

    class _Capture(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, *args, **kwargs):
            seen.append(messages)
            return super()._generate(messages, *args, **kwargs)

    def _gen():
        while True:
            yield AIMessage(content="done")

    model = _Capture(messages=_gen())
    code.build_agent(workdir, model).invoke({"messages": [HumanMessage("hi")]},
                                            {"recursion_limit": 10})
    return str(seen[0][0].content)


def test_the_memory_file_is_read_from_the_workspace(tmp_path: Path):
    """What the agent is told about the project is the *project's* file. The
    workspace is usually not this repository, so this is some other project
    speaking, and the path is the one the agent could open itself."""
    (tmp_path / "AGENTS.md").write_text("Never touch vendor/.\n", encoding="utf-8")

    assert code.memory_file(tmp_path) == ["/AGENTS.md"]
    assert "Never touch vendor/." in _system_prompt_of_a_run(tmp_path)


def test_only_one_memory_file_is_read_when_a_project_has_both(tmp_path: Path):
    """A repository holding both holds two drafts of one document. Loading
    both pays for the overlap twice and leaves the model to work out which
    draft is current -- so the first match wins and the other is not read.

    `CLAUDE.md` is first because that is the file the projects this harness
    works on actually keep current."""
    (tmp_path / "CLAUDE.md").write_text("the current one\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("the stale one\n", encoding="utf-8")

    assert code.memory_file(tmp_path) == ["/CLAUDE.md"]
    prompt_text = _system_prompt_of_a_run(tmp_path)
    assert "the current one" in prompt_text
    assert "the stale one" not in prompt_text


def test_a_project_that_only_wrote_a_claude_md_still_has_a_memory_file(tmp_path: Path):
    """`AGENTS.md` is the spec, but most projects that wrote instructions for
    an agent wrote them for Claude Code. Preferring one is not requiring it."""
    (tmp_path / "CLAUDE.md").write_text("Run `pytest -q`.\n", encoding="utf-8")

    assert code.memory_file(tmp_path) == ["/CLAUDE.md"]
    assert "Run `pytest -q`." in _system_prompt_of_a_run(tmp_path)


def test_a_workspace_with_neither_is_told_nothing_about_memory(tmp_path: Path):
    """The section is a fact about the workspace, not a fixed part of the
    prompt: with no file there is no middleware and nothing is charged."""
    assert code.memory_file(tmp_path) == []
    assert "<agent_memory>" not in _system_prompt_of_a_run(tmp_path)


def test_the_memory_section_is_ours_not_the_frameworks(tmp_path: Path):
    """deepagents' default is ~4,500 characters about a user to ask, to learn
    preferences from and to be interrupted by. Nobody is watching this run, and
    it is charged on every call ([6.6](../../docs/06-agent.md#66-skills) makes
    the same argument for the skills section)."""
    (tmp_path / "AGENTS.md").write_text("Never touch vendor/.\n", encoding="utf-8")

    prompt_text = _system_prompt_of_a_run(tmp_path)
    assert "memory_guidelines" not in prompt_text, "the framework's default"
    assert "What this project asks of you" in prompt_text
