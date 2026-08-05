"""Checks for the ad-hoc harness: state machine, context caps, tool wiring.

Driven by a scripted fake model, so the whole loop is exercised without
spending pool quota. No framework: `python -m tests.agent.test_harness`.
"""

import tempfile
from pathlib import Path

from langchain_core.messages import AIMessage

from agent.harness.blackboard import Blackboard, MAX_NOTE_CHARS
from agent.harness.loop import Stats, run_role, solve, _parse_route, _harvest_paths, RoleResult
from agent.harness.roles import ROLES
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
    bb.set_test("boom", False)
    rendered = bb.render(("task", "files"))
    check("undeclared section absent", "boom" not in rendered)
    check("declared section present", "/a.py" in rendered)


def test_role_isolation():
    """Each role gets only its own tools -- the whole point of the split."""
    root = seed_project()
    backend = RestrictedShellBackend(root_dir=str(root))
    toolset = make_tools(backend)
    bb = Blackboard(task="fix add")
    model = ScriptedModel([ai("no files")])

    run_role(model, ROLES["locate"], toolset, bb, {}, Stats())
    check("locate has no edit tool", "replace_in_file" not in model.seen[0]["tools"])
    check("locate has search", "search_code" in model.seen[0]["tools"])

    model2 = ScriptedModel([ai("EDIT")])
    run_role(model2, ROLES["route"], toolset, bb, {}, Stats())
    check("route is given no tools at all", not model2.seen[0]["tools"])


def test_route_parsing():
    bb = Blackboard(task="t")
    check("plain word", _parse_route("EDIT", bb) == "edit")
    check("chatty model", _parse_route("I think we should INSPECT again.", bb) == "inspect")
    check("giveup", _parse_route("GIVEUP", bb) == "giveup")
    check("unparseable falls back to edit", _parse_route("hmm, not sure", bb) == "edit")


def test_path_harvest():
    r = RoleResult(text="", outputs=[("search_code", "/pkg/calc.py:2:    return a - b")])
    check("path harvested from tool output", _harvest_paths(r) == ["/pkg/calc.py"])


def test_end_to_end_pass():
    """locate -> inspect -> edit -> test(deterministic) -> pass."""
    root = seed_project()
    backend = RestrictedShellBackend(root_dir=str(root))
    toolset = make_tools(backend)
    model = ScriptedModel([
        ai(calls=[("search_code", {"pattern": "def add"})]),      # locate
        ai("found it"),
        ai(calls=[("read_lines", {"file_path": "/calc.py"})]),    # inspect
        ai("/calc.py returns a - b, should be a + b"),
        ai(calls=[("replace_in_file", {"file_path": "/calc.py",   # edit
                                       "old_text": "return a - b",
                                       "new_text": "return a + b"})]),
        ai("applied"),
    ])
    bb, stats, outcome = solve(model, backend, toolset, "add() is wrong", max_cycles=3)
    check(f"outcome was {outcome}", outcome == "pass")
    check("file actually fixed", (root / "calc.py").read_text() == FIXED)
    check("test step spent no model call", "test" not in stats.by_role)
    check("edit recorded", any("calc.py" in e for e in bb.edits))


def test_failing_edit_routes():
    """A failed test must reach `route`, and route must not hold tools."""
    root = seed_project()
    backend = RestrictedShellBackend(root_dir=str(root))
    toolset = make_tools(backend)
    model = ScriptedModel([
        ai("none"),                                                # locate
        ai("/calc.py is wrong"),                                   # inspect
        ai("could not edit"),                                      # edit (no tool call)
        ai("GIVEUP"),                                              # route
    ])
    bb, stats, outcome = solve(model, backend, toolset, "fix it", max_cycles=3)
    check(f"outcome was {outcome}", outcome == "giveup")
    check("route was consulted", "route" in stats.by_role)
    check("a failing test was recorded", bb.test_passed is False)


def test_prompt_stays_small():
    """The budget claim: a role's prompt must fit the smallest pool member."""
    root = seed_project()
    backend = RestrictedShellBackend(root_dir=str(root))
    toolset = make_tools(backend)
    bb = Blackboard(task="fix add")
    for i in range(30):                       # simulate a long run
        bb.add_note(f"finding number {i} " + "y" * 400)
        bb.add_files([f"/mod{i}.py"])
        bb.add_edit(f"ok: edit {i}")
    bb.set_test("E   assert 1 == 3\n" * 400, False)

    worst = 0
    for name in ("locate", "inspect", "edit", "route"):
        role = ROLES[name]
        model = ScriptedModel([ai("ok")])
        stats = Stats()
        run_role(model, role, toolset, bb, {}, stats)
        worst = max(worst, stats.prompt_tokens)
    # 5,400 = the 6,000-TPM pool member's usable ceiling after the router's
    # 0.9 fit margin. Saturated state must still fit it.
    check(f"worst-case role prompt {worst} tok must fit a 6k-TPM model", worst < 5_400)
    return worst


if __name__ == "__main__":
    test_blackboard_caps()
    test_role_isolation()
    test_route_parsing()
    test_path_harvest()
    test_end_to_end_pass()
    test_failing_edit_routes()
    worst = test_prompt_stays_small()
    print(f"harness: all checks passed (worst-case role prompt ~{worst} tokens)")
