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

from agent.harness.log import Log, MAX_NOTES, MAX_NOTE_CHARS
from agent.harness.nodes import ORCHESTRATOR, WORKERS, Stats, run_node
from agent.runtime.tools import make_tools
from agent.runtime.backend import RestrictedShellBackend

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


def test_log_caps():
    """Clipped on the way in, capped on the way out, and never the whole log."""
    log = Log(task="t")
    log.note("explore", "x" * (MAX_NOTE_CHARS + 500))
    check("a note is clipped as it is written",
          len(log.texts("notes")[0]) <= MAX_NOTE_CHARS + 20)

    for i in range(20):
        log.note("explore", f"note {i}")
    shown = log.view(("notes",))[0].content
    check("but only the last few are shown", shown.count("[explore]") <= MAX_NOTES)
    check("and they are the last few", "note 19" in shown and "note 0" not in shown)

    log.files("explore", ["/a.py", "/a.py", "/b.py"])
    check("files deduped", log.texts("files").count("- /a.py") == 1)

    # A node only ever sees the kinds it declares.
    log.ran("execute", "boom", False)
    rendered = "\n".join(str(m.content) for m in log.view(("task", "files")))
    check("undeclared kind absent", "boom" not in rendered)
    check("declared kind present", "/a.py" in rendered)


def test_role_isolation():
    """Each role gets only its own tools -- the whole point of the split.

    The orchestrator holding none is the load-bearing case: the call that
    decides what happens next must not be able to make anything happen.
    """
    root = seed_project()
    backend = RestrictedShellBackend(root_dir=str(root), allow_git=True)
    toolset = make_tools(backend)
    log = Log(task="fix add")

    model = ScriptedModel([ai("nothing found")])
    run_node(model, WORKERS["explore"], toolset, log, {}, Stats())
    check("explore cannot edit", "replace_in_file" not in model.seen[0]["tools"])
    check("explore can search", "search_code" in model.seen[0]["tools"])

    writer = ScriptedModel([ai("applied")])
    run_node(writer, WORKERS["write"], toolset, log, {}, Stats())
    check("write cannot run anything", "run_command" not in writer.seen[0]["tools"])
    check("write can read, or replace_in_file cannot match",
          "read_lines" in writer.seen[0]["tools"])

    hub = ScriptedModel([ai("ACTION: EXPLORE")])
    run_node(hub, ORCHESTRATOR, toolset, log, {}, Stats())
    check("the orchestrator is given no tools at all", not hub.seen[0]["tools"])


def test_a_role_never_sees_another_role_s_conversation():
    """`run_node` throws its message list away; only the NodeResult escapes.

    This is what bounds a prompt by the role's declaration rather than by how
    long the run has been going, and it is why the loop is hand-written instead
    of a prebuilt agent that accumulates messages.
    """
    root = seed_project()
    backend = RestrictedShellBackend(root_dir=str(root))
    toolset = make_tools(backend)
    log = Log(task="fix add")

    first = ScriptedModel([
        ai(calls=[("search_code", {"pattern": "def add"})]),
        ai("found it in /calc.py"),
    ])
    run_node(first, WORKERS["explore"], toolset, log, {}, Stats())

    second = ScriptedModel([ai("ok")])
    run_node(second, WORKERS["write"], toolset, log, {}, Stats())
    rendered = "\n".join(str(m.content) for m in second.seen[0]["messages"])
    check("the earlier role's tool call did not leak", "search_code" not in rendered)
    check("nor did its answer", "found it in" not in rendered)
