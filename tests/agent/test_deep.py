"""The deepagents harness's configuration, checked without the network.

Everything here is about what the agent is *told* and what it is *allowed*,
which is the part of the configuration that can be wrong silently. Whether the
agent then does good work is the scenarios' question, not this file's.
"""

import re
from pathlib import Path

from langchain_core.messages import ToolMessage

from agent.deep import context, prompt
from agent.deep.shell import ShellAllowListMiddleware


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
