"""Checks for the ad-hoc harness: state machine, context caps, tool wiring.

Driven by a scripted fake model, so the whole loop is exercised without
spending pool quota. No framework: `python -m tests.agent.test_harness`.
"""

import tempfile
from pathlib import Path

from langchain_core.messages import AIMessage

from agent.harness.blackboard import Blackboard, MAX_NOTE_CHARS
from agent.harness.loop import (Stats, run_role, seed_files, solve, _parse_route,
                                _harvest_paths, RoleResult)
from agent.harness.roles import ROLES
from agent.harness.tools import make_tools
from agent.harness.variants import V1, V2, V3, V4, V5, V6, V7, VARIANTS, get
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
    check("a failing test was recorded", bb.exec_ok is False)


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
    bb.set_exec("E   assert 1 == 3\n" * 400, False)

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


def test_seeding():
    """v2/v5 replace the `locate` role with a glob on small trees."""
    root = seed_project()
    backend = RestrictedShellBackend(root_dir=str(root))
    found = seed_files(backend, limit=25)
    check("seeds both files", sorted(found) == ["/calc.py", "/test_calc.py"])
    check("declines when over the limit", seed_files(backend, limit=1) == [])


class RoleAwareModel(ScriptedModel):
    """Answers according to which role is asking, not from a fixed queue.

    A fixed script cannot compare architectures: each variant consumes it in a
    different order, so the test ends up measuring script drift rather than
    call count. Keying the response off the role holds *model behaviour*
    constant while the architecture varies, which is the comparison we want.
    """

    def __init__(self, first_round=None):
        super().__init__([])
        self.round = {}
        self.first_round = first_round or {}

    def bind_tools(self, tools, **kw):
        clone = RoleAwareModel()
        clone.round, clone.first_round, clone.seen = self.round, self.first_round, self.seen
        clone.bound = [t.name for t in tools]
        return clone

    def invoke(self, messages, config=None, **kw):
        self.seen.append({"messages": messages, "tools": self.bound})
        role = self._role_of(messages)
        n = self.round.get(role, 0)
        self.round[role] = n + 1
        # Round 0: make the tool call the role exists to make. Round 1+: report.
        if n == 0 and role in self.first_round:
            return self.first_round[role]
        return AIMessage(content={"route": "GIVEUP"}.get(role, "done"))

    @staticmethod
    def _role_of(messages):
        text = str(messages[0].content)
        if "no tools" in text:
            return "route"
        if "find the files" in text.lower() or "you find the files" in text.lower():
            return "locate"
        if "apply one code change" in text:
            return "edit"
        return "inspect"      # covers `investigate` too


def test_variant_call_counts():
    """The point of each variant is its call count. Model behaviour is held
    constant; only the architecture differs, so this table isolates it."""
    rows = {}
    # Pipeline variants only: the orchestrated topology has different roles and
    # its own test, and a role-keyed fake built for one cannot drive the other.
    for name, variant in VARIANTS.items():
        if variant.topology != "pipeline":
            continue
        root = seed_project()
        backend = RestrictedShellBackend(root_dir=str(root))
        toolset = make_tools(backend)
        model = RoleAwareModel(first_round={
            "locate": ai(calls=[("search_code", {"pattern": "def add"})]),
            "inspect": ai(calls=[("read_lines", {"file_path": "/calc.py"})]),
            "edit": ai(calls=[("replace_in_file", {"file_path": "/calc.py",
                                                   "old_text": "return a - b",
                                                   "new_text": "return a + b"})]),
        })
        bb, stats, outcome = solve(model, backend, toolset, "add() is wrong",
                                   max_cycles=3, variant=variant)
        rows[name] = (outcome, stats.calls, sorted(stats.by_role))
        check(f"{name} should pass", outcome == "pass")
        check(f"{name} fixed the file", (root / "calc.py").read_text() == FIXED)

    check("v2 skips locate", "locate" not in rows["v2-seeded"][2])
    check("v3 uses investigate", "investigate" in rows["v3-merged"][2])
    check("v5 skips locate", "locate" not in rows["v5-lean"][2])
    check("seeding removes calls", rows["v2-seeded"][1] < rows["v1-pipeline"][1])
    check("merging removes calls", rows["v3-merged"][1] < rows["v1-pipeline"][1])
    return rows


def test_fast_retry():
    """v4 must retry the edit once before spending a call on `route`."""
    root = seed_project()
    backend = RestrictedShellBackend(root_dir=str(root))
    toolset = make_tools(backend)
    model = ScriptedModel([
        ai("none"),                 # locate
        ai("/calc.py is wrong"),    # inspect
        ai("cannot edit"),          # edit  -> test fails
        ai("still cannot"),         # edit again (deterministic retry, no route)
        ai("GIVEUP"),               # route
    ])
    bb, stats, outcome = solve(model, backend, toolset, "fix", max_cycles=4, variant=V4)
    check("edit ran twice before routing", stats.by_role["edit"]["calls"] >= 2)
    check("route consulted only after the retry", stats.by_role["route"]["calls"] == 1)
    check("retry recorded", any("retry" in line for line in bb.log))


def test_orchestrated_topology():
    """Hub and spoke: the orchestrator picks each worker, holds no tools, and
    a DONE it cannot back with a successful run is refused once."""
    root = seed_project()
    backend = RestrictedShellBackend(root_dir=str(root))
    toolset = make_tools(backend)

    # Claim DONE immediately. The guard must force an execute first; that run
    # fails (the bug is still there), so the next DONE must not end the task.
    script = [ai("DONE"),                                        # orchestrate
              ai(calls=[("run_tests", {"command": "python -m pytest"})]),
              ai("it failed"),                                   # execute wrap-up
              ai("EDIT"),                                        # orchestrate
              ai(calls=[("replace_in_file", {"file_path": "/calc.py",
                                             "old_text": "return a - b",
                                             "new_text": "return a + b"})]),
              ai("done"),
              ai("EXECUTE"),                                     # orchestrate
              ai(calls=[("run_tests", {"command": "python -m pytest"})]),
              ai("passed"),
              ai("DONE")]                                        # orchestrate
    model = ScriptedModel(script)
    bb, stats, outcome = solve(model, backend, toolset, "add() is wrong",
                               max_cycles=8, variant=V7)

    check(f"outcome was {outcome}", outcome == "pass")
    check("file fixed", (root / "calc.py").read_text() == FIXED)
    check("orchestrator ran repeatedly", stats.by_role["orchestrate"]["calls"] >= 3)
    check("execute is its own role", "execute" in stats.by_role)
    check("unverified done was refused",
          any("nothing has run" in line for line in bb.log))
    # The orchestrator must never hold tools, in any topology. `bind_tools` is
    # not called at all for a tool-less role, so `tools` stays None -- a
    # stronger guarantee than binding an empty list.
    check("orchestrator had no tools", any(not s["tools"] for s in model.seen))


def test_execute_trusts_exit_code_not_the_model():
    """A model claiming success about a failing run must not end the task."""
    root = seed_project()
    backend = RestrictedShellBackend(root_dir=str(root))
    toolset = make_tools(backend)
    model = ScriptedModel([
        ai("EXECUTE"),
        ai(calls=[("run_tests", {"command": "python -m pytest"})]),
        ai("Everything passes, we are done!"),   # false claim about a failing run
        ai("DONE"),
    ])
    bb, stats, outcome = solve(model, backend, toolset, "fix", max_cycles=3, variant=V7)
    check("exit code beats the model's summary", bb.exec_ok is False)
    check("task not marked pass", outcome != "pass")


def test_orchestrator_fallback_is_read_only():
    """An unintelligible orchestrator must fall back to the read-only worker."""
    from agent.harness.loop import _parse_action
    bb = Blackboard(task="t")
    check("plain", _parse_action("EDIT", bb) == "edit")
    check("chatty", _parse_action("Let's EXECUTE the suite now", bb) == "execute")
    check("garbage falls back to explore", _parse_action("uhh", bb) == "explore")


def test_variant_lookup():
    check("default", get(None) is V1)
    check("by name", get("v5-lean") is V5)
    try:
        get("nope")
    except SystemExit:
        return
    raise AssertionError("unknown variant must fail loudly, not fall back")


if __name__ == "__main__":
    test_blackboard_caps()
    test_role_isolation()
    test_route_parsing()
    test_path_harvest()
    test_end_to_end_pass()
    test_failing_edit_routes()
    test_seeding()
    test_fast_retry()
    test_orchestrated_topology()
    test_execute_trusts_exit_code_not_the_model()
    test_orchestrator_fallback_is_read_only()
    test_variant_lookup()
    rows = test_variant_call_counts()
    worst = test_prompt_stays_small()
    print(f"harness: all checks passed (worst-case role prompt ~{worst} tokens)\n")
    print(f"{'variant':<14}{'outcome':<9}{'calls':>6}  roles")
    for name, (outcome, calls, roles) in rows.items():
        print(f"{name:<14}{outcome:<9}{calls:>6}  {', '.join(roles)}")
