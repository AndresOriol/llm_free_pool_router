"""The coding agent's configuration, checked without the network.

Everything here is about what the agent is *told* and what it is *allowed*,
which is the part of the configuration that can be wrong silently. Whether the
agent then does good work is the scenarios' question, not this file's.
"""

import re
from pathlib import Path

from langchain_core.messages import ToolMessage

from agent.code import context, prompt
from agent.code.shell import ShellAllowListMiddleware


class _Request:
    """The bit of ToolCallRequest the middleware actually reads."""

    def __init__(self, name, args):
        self.tool_call = {"name": name, "args": args, "id": "call_1"}


def _refuse(middleware, command):
    return middleware._refusal(_Request("execute", {"command": command}))


def test_prompt_leaves_no_placeholder_unreplaced():
    # A missed placeholder ships literal `{braces}` to the model, which reads as
    # an instruction it cannot follow rather than as a bug.
    text = prompt.build(128_000, members=14)
    assert not re.findall(r"\{[a-z_]+\}", text)


def test_prompt_states_the_floor_not_a_model_name():
    text = prompt.build(128_000, members=14)
    assert "128,000 input tokens" in text
    assert "pool of models" in text


def test_prompt_roots_paths_at_the_jail():
    # The backend runs virtual_mode=True, so telling the model to build host
    # paths -- which is what dcode's prompt does -- would fail every tool call.
    text = prompt.build(128_000)
    assert "rooted at `/`" in text
    assert "C:\\Users\\..." in text


def test_prompt_forbids_editing_the_thing_that_contradicts_the_task(monkeypatch):
    """The one behaviour the guard exists to stop.

    A run in the first full-set batch was told to write a field onto objects the
    documentation says are never modified. It mutated them, replaced the test
    guarding the invariant with one asserting the opposite, and deleted the
    guarantee from the page.
    """
    monkeypatch.delenv("AGENT_INVARIANT_GUARD", raising=False)
    text = prompt.build(128_000)
    assert "## Contradicted Requests" in text
    assert "Never edit a test or a document so that it stops contradicting you" in text


def test_the_guard_overrides_the_ambiguity_guidance_explicitly(monkeypatch):
    """Both are in the prompt and they point opposite ways on this case.

    A headless run is told to pick a reading and proceed, which is right for
    ambiguity and is the wrong instruction in front of a stated guarantee. The
    section has to say which one wins, or it is one more thing to weigh.
    """
    monkeypatch.delenv("AGENT_INVARIANT_GUARD", raising=False)
    text = prompt.build(128_000)
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
    text = prompt.build(128_000)
    assert "Contradicted Requests" not in text
    assert not re.findall(r"\{[a-z_]+\}", text)


def test_the_guard_distinguishes_explicit_doc_and_spec_updates(monkeypatch):
    """Explicit requests to update docs or migrate specifications are valid work.

    The invariant guard forbids unrequested edits that hide contradictions, but
    must explicitly allow tasks that ask to update documentation or migrate code
    and specs to a new data model version.
    """
    monkeypatch.delenv("AGENT_INVARIANT_GUARD", raising=False)
    text = prompt.build(128_000)
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
    flat = " ".join(prompt.build(128_000).split())

    assert "Do not create summary markdown files" in flat
    assert "feedback file is the exception" in flat
    assert "NOTES.md" in flat


def test_the_account_rule_asks_for_what_happened_not_what_was_hoped(monkeypatch):
    """C4: a confidently wrong rationale reads exactly like a correct one.

    `wrote_account` counts lines and cannot see this, so the prompt has to.
    """
    monkeypatch.delenv("AGENT_WRITE_ACCOUNT", raising=False)
    flat = " ".join(prompt.build(128_000).split())

    assert "Write what happened, not what was hoped for" in flat
    assert "anything you left undone" in flat


def test_the_account_rule_is_a_configuration_that_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("AGENT_WRITE_ACCOUNT", "0")
    text = prompt.build(128_000)

    assert "feedback file is the exception" not in text
    assert "Do not create summary markdown files" in text
    assert not re.findall(r"\{[a-z_]+\}", text)


def test_shell_allows_an_allowlisted_program():
    middleware = ShellAllowListMiddleware(["python", "pytest"])
    assert _refuse(middleware, "python -m pytest -q") is None


def test_shell_refuses_an_unlisted_program():
    middleware = ShellAllowListMiddleware(["python"])
    message = _refuse(middleware, "rm -rf /")
    assert isinstance(message, ToolMessage)
    assert message.status == "error"
    # The refusal has to say what *is* allowed, or the model's next step is a
    # guess -- the whole reason this returns a message instead of raising.
    assert "python" in message.content


def test_shell_refuses_a_program_named_by_path():
    middleware = ShellAllowListMiddleware(["python"])
    message = _refuse(middleware, "./evil.sh")
    assert isinstance(message, ToolMessage)
    assert "bare" in message.content


def test_shell_ignores_tools_that_are_not_execute():
    middleware = ShellAllowListMiddleware(["python"])
    assert middleware._refusal(_Request("read_file", {"file_path": "/a.py"})) is None


def test_shell_rejects_an_empty_allow_list():
    # An empty list would refuse everything while reading like a policy.
    try:
        ShellAllowListMiddleware([])
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_tree_skips_caches_and_dotfiles(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("x = 1")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "junk.pyc").write_text("junk")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "lib.py").write_text("junk")

    listing = context.tree(tmp_path)
    assert "/pkg/mod.py" in listing
    assert "__pycache__" not in listing
    assert ".venv" not in listing


def test_tree_truncates_rather_than_flooding_context(tmp_path: Path):
    for i in range(50):
        (tmp_path / f"f{i}.py").write_text("x = 1")
    listing = context.tree(tmp_path, max_entries=10)
    assert "listing stopped at 10 entries" in listing
    assert len(listing.splitlines()) == 11
