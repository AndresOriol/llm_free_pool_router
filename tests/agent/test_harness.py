"""Unit checks for the harness's two load-bearing mechanisms, plus the fixtures.

The mechanisms are context partitioning (a role sees only what it declares) and
tool partitioning (a role holds only what it needs). Everything else about the
harness is the graph, and that is tested in test_session.py.

Driven by a scripted fake model, so nothing here spends pool quota.

    python -m pytest tests/agent/test_harness.py
"""

import tempfile
from pathlib import Path

from langchain_core.messages import AIMessage

from agent.harness.blackboard import Blackboard, MAX_NOTE_CHARS
from agent.harness.roles import ORCHESTRATE, ROLES
from agent.harness.runner import Stats, run_role
from agent.harness.tools import make_tools
from agent.restricted_backend import RestrictedShellBackend

BROKEN = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"


def seed_project() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "calc.py").write_text(BROKEN)
    (root / "test_calc.py").write_text(TEST)
    return root


class ScriptedModel:
    """A BaseChatModel stand-in that replays a fixed list of responses and
    records every prompt it was handed."""

    def __init__(self, script):
        self.script = list(script)
        self.seen = []
        self.bound = None

    def bind_tools(self, tools, **kw):
        clone = ScriptedModel(self.script)
        clone.script = self.script          # share the queue
        clone.seen = self.seen              # share the record
        clone.bound = [t.name for t in tools]
        return clone

    def invoke(self, messages, config=None, **kw):
        self.seen.append({"messages": messages, "tools": self.bound})
        return self.script.pop(0) if self.script else AIMessage(content="done")


def ai(text="", calls=()):
    return AIMessage(content=text, tool_calls=[
        {"name": n, "args": a, "id": f"c{i}"} for i, (n, a) in enumerate(calls)])


def check(label, cond):
    if not cond:
        raise AssertionError(label)


def test_blackboard_caps():
    bb = Blackboard(task="t")
    bb.add_note("x" * (MAX_NOTE_CHARS + 500))
    check("note is clipped", len(bb.notes[0]) <= MAX_NOTE_CHARS + 20)
    for i in range(20):
        bb.add_note(f"note {i}")
    check("notes are capped", len(bb.notes) <= 6)
    bb.add_files(["/a.py", "/a.py", "/b.py"])
    check("files deduped", bb.files.count("/a.py") == 1)

    # A role only ever sees the sections it declares.
    bb.set_exec("boom", False)
    rendered = bb.render(("task", "files"))
    check("undeclared section absent", "boom" not in rendered)
    check("declared section present", "/a.py" in rendered)


def test_role_isolation():
    """Each role gets only its own tools -- the whole point of the split.

    The orchestrator holding none is the load-bearing case: the call that
    decides what happens next must not be able to make anything happen.
    """
    root = seed_project()
    backend = RestrictedShellBackend(root_dir=str(root), allow_git=True)
    toolset = make_tools(backend)
    bb = Blackboard(task="fix add")

    model = ScriptedModel([ai("nothing found")])
    run_role(model, ROLES["explore"], toolset, bb, {}, Stats())
    check("explore cannot edit", "replace_in_file" not in model.seen[0]["tools"])
    check("explore can search", "search_code" in model.seen[0]["tools"])

    writer = ScriptedModel([ai("applied")])
    run_role(writer, ROLES["write"], toolset, bb, {}, Stats())
    check("write cannot run anything", "run_command" not in writer.seen[0]["tools"])
    check("write can read, or replace_in_file cannot match",
          "read_lines" in writer.seen[0]["tools"])

    hub = ScriptedModel([ai("ACTION: EXPLORE")])
    run_role(hub, ORCHESTRATE, toolset, bb, {}, Stats())
    check("the orchestrator is given no tools at all", not hub.seen[0]["tools"])


def test_a_role_never_sees_another_role_s_conversation():
    """`run_role` throws its message list away; only the RoleResult escapes.

    This is what bounds a prompt by the role's declaration rather than by how
    long the run has been going, and it is why the loop is hand-written instead
    of a prebuilt agent that accumulates messages.
    """
    root = seed_project()
    backend = RestrictedShellBackend(root_dir=str(root))
    toolset = make_tools(backend)
    bb = Blackboard(task="fix add")

    first = ScriptedModel([
        ai(calls=[("search_code", {"pattern": "def add"})]),
        ai("found it in /calc.py"),
    ])
    run_role(first, ROLES["explore"], toolset, bb, {}, Stats())

    second = ScriptedModel([ai("ok")])
    run_role(second, ROLES["write"], toolset, bb, {}, Stats())
    rendered = "\n".join(str(m.content) for m in second.seen[0]["messages"])
    check("the earlier role's tool call did not leak", "search_code" not in rendered)
    check("nor did its answer", "found it in" not in rendered)
