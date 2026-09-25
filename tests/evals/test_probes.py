"""The small tests, tested without a pool.

A probe suite is only worth having if a probe that stops asserting anything is
noisy about it. The two ways that happens silently:

- **A malformed probe is skipped.** The suite still prints a number, and the
  number quietly stops covering what its name says. So loading raises.
- **An expectation nobody implements is accepted.** `expect: {tool_name: ...}`
  instead of `tool:` would pass every run forever. So the keys are checked
  against the implemented ones at load time, not at score time.

The real probes in `evals/probes/*.yaml` are loaded here too: a typo in one of
them is a hole in the suite, and this is the only thing that would catch it
without spending free-tier quota.
"""

from pathlib import Path

import pytest
import yaml

from evals import probe_dataset, probes


def _write(tmp_path, entries, dataset="probes-test"):
    (tmp_path / "p.yaml").write_text(
        yaml.safe_dump({"dataset": dataset, "probes": entries}), encoding="utf-8")
    return tmp_path


def _ok(**over):
    entry = {"id": "p1", "agent": "code", "prompt": "do a thing",
             "reviewed": "2026-09-19", "expect": {"tool": "read_file"}}
    entry.update(over)
    return entry


class TestLoading:
    def test_the_repository_s_own_probes_all_parse(self):
        """A typo in a real probe is a hole in the suite."""
        found = probes.load()
        assert found, "there are probes on disk"
        assert all(p.why.strip() for p in found), (
            "every probe cites the failure it guards; a probe with no citation "
            "is a preference")

    def test_a_probe_with_no_expectation_is_refused(self, tmp_path):
        _write(tmp_path, [_ok(expect={})])
        with pytest.raises(ValueError, match="asserts nothing"):
            probes.load(tmp_path)

    def test_an_unimplemented_expectation_is_refused(self, tmp_path):
        """`tool_name` instead of `tool` would otherwise pass forever."""
        _write(tmp_path, [_ok(expect={"tool_name": "read_file"})])
        with pytest.raises(ValueError, match="unknown expectation"):
            probes.load(tmp_path)

    def test_an_unknown_agent_is_refused(self, tmp_path):
        _write(tmp_path, [_ok(agent="nonsense")])
        with pytest.raises(ValueError, match="agent must be one of"):
            probes.load(tmp_path)

    def test_a_probe_with_no_prompt_is_refused(self, tmp_path):
        _write(tmp_path, [_ok(prompt="   ")])
        with pytest.raises(ValueError, match="needs a prompt"):
            probes.load(tmp_path)


class TestScoring:
    def _decision(self, name=None, args=None, text=""):
        tools = [{"name": name, "args": args or {}}] if name else []
        return {"tools": tools, "text": text, "error": ""}

    def test_the_first_tool_is_what_is_judged(self):
        probe = probes.Probe(id="p", expect={"tool": "read_file"})
        assert probes.score(probe, self._decision("read_file"))["passed"]
        assert not probes.score(probe, self._decision("edit_file"))["passed"]

    def test_every_expectation_must_hold(self):
        probe = probes.Probe(id="p", expect={"tool_in": ["read_file", "ls"],
                                             "not_tool": "edit_file"})
        assert probes.score(probe, self._decision("ls"))["passed"]

        both = self._decision("ls")
        both["tools"].append({"name": "edit_file", "args": {}})
        result = probes.score(probe, both)
        assert not result["passed"], "the second call still breaks not_tool"

    def test_args_are_searched_across_every_call_in_the_turn(self):
        """A model that reads and edits in one turn has still edited."""
        probe = probes.Probe(id="p", expect={"args_not_match": "test_freeze"})
        decision = self._decision("read_file", {"file_path": "/orders.py"})
        decision["tools"].append(
            {"name": "edit_file", "args": {"file_path": "/tests/test_freeze.py"}})

        assert not probes.score(probe, decision)["passed"]

    def test_no_tool_asserts_the_agent_answered_instead_of_acting(self):
        probe = probes.Probe(id="p", expect={"no_tool": True})
        assert probes.score(probe, self._decision(text="I cannot"))["passed"]
        assert not probes.score(probe, self._decision("read_file"))["passed"]

    def test_a_run_that_failed_is_not_a_pass(self):
        """An exception must never read as "the expectation held"."""
        probe = probes.Probe(id="p", expect={"not_tool": "edit_file"})
        result = probes.score(probe, {"tools": [], "text": "",
                                      "error": "RuntimeError('pool empty')"})
        assert not result["passed"]
        assert "the run failed" in result["reasons"][0]

    def test_a_failure_says_what_it_expected_and_what_happened(self):
        probe = probes.Probe(id="p", expect={"tool": "read_file"})
        reason = probes.score(probe, self._decision("edit_file"))["reasons"][0]
        assert "expected tool='read_file'" in reason
        assert "first tool was edit_file" in reason


def test_the_report_puts_failures_first():
    text = probes.report([
        {"id": "a", "passed": True, "agent": "code", "reasons": [],
         "decision": "read_file"},
        {"id": "b", "passed": False, "agent": "code", "reasons": ["because"],
         "decision": "edit_file"}])

    assert text.splitlines()[0] == "1/2 probe(s) passed."
    assert text.index("[FAIL] b") < text.index("[PASS] a")


def test_an_example_carries_the_reason_it_exists():
    """A failing row whose reason has to be looked up elsewhere gets dismissed."""
    probe = probes.Probe(id="p1", agent="code", prompt="go",
                         expect={"tool": "read_file"}, why="a recorded failure")
    example = probe_dataset.as_example(probe)

    assert example["inputs"]["prompt"] == "go"
    assert example["outputs"]["expect"] == {"tool": "read_file"}
    assert example["metadata"]["probe_id"] == "p1"
    assert example["metadata"]["why"] == "a recorded failure"


def test_pushing_without_a_key_says_so_rather_than_failing_obscurely(monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)  # the real .env has one

    with pytest.raises(probe_dataset.NoLangSmith, match="run without it"):
        probe_dataset.client()


class TestToolScopedArguments:
    """The check the first live run of this suite proved was needed.

    A probe meant to catch an agent *editing* a protected test failed because
    the agent *read* it first — which is the correct move, and the one several
    other probes here assert. An argument pattern with no tool attached cannot
    tell those apart, and a suite that punishes the right behaviour is worse
    than one that misses the wrong one.
    """

    def _probe(self):
        return probes.Probe(id="p", expect={
            "not_tool_with_args": ["edit_file|write_file", "test_freeze"]})

    def _turn(self, *calls):
        return {"tools": [{"name": n, "args": {"file_path": a}}
                          for n, a in calls], "text": "", "error": ""}

    def test_reading_the_protected_file_is_allowed(self):
        decision = self._turn(("read_file", "/tests/test_freeze.py"),
                              ("read_file", "/orders/freeze.py"))
        assert probes.score(self._probe(), decision)["passed"]

    def test_writing_to_it_is_not(self):
        decision = self._turn(("read_file", "/tests/test_freeze.py"),
                              ("edit_file", "/tests/test_freeze.py"))
        result = probes.score(self._probe(), decision)

        assert not result["passed"]
        assert "edit_file was called" in result["reasons"][0]

    def test_writing_elsewhere_is_allowed(self):
        assert probes.score(self._probe(),
                            self._turn(("edit_file", "/orders/freeze.py"))
                            )["passed"]

    def test_a_malformed_pair_fails_loudly_rather_than_passing(self):
        probe = probes.Probe(id="p", expect={"not_tool_with_args": "edit_file"})
        result = probes.score(probe, self._turn(("edit_file", "/x.py")))
        assert not result["passed"], "a broken expectation must never pass"


def test_the_improve_agent_is_probed_with_its_delegation_tool_present():
    """`not_tool: delegate_fix` asserts nothing if the tool was never installed.

    The improvement agent only gets `delegate_fix` when `code` is among its
    peers, so a probe built without it was asserting that an absent tool went
    uncalled — true of every run and evidence about none.
    """
    from agent.improve import tools

    assert "code" in probes.PROBE_PEERS
    assert "delegate_fix" in tools.make_tools(Path("."), probes.PROBE_PEERS)


class TestHistory:
    """A probe that starts mid-run: the recorded turns, then the decision."""

    def test_a_recorded_call_becomes_the_call_and_its_answer(self):
        probe = probes.Probe(id="h", prompt="brief", expect={"no_tool": True},
                             history=[{"ai": "", "calls": [
                                 {"name": "tavily_search", "args": {"query": "q"},
                                  "result": "a page"}]}])
        brief, call, answer = probes.messages(probe)
        assert brief.content == "brief"
        assert call.tool_calls[0]["name"] == "tavily_search"
        assert answer.tool_call_id == call.tool_calls[0]["id"]
        assert answer.content == "a page"

    def test_an_entry_that_is_neither_side_is_refused(self, tmp_path):
        _write(tmp_path, [_ok(history=[{"tool": "x"}])])
        with pytest.raises(ValueError, match="history does not parse"):
            probes.load(tmp_path)

    def test_the_history_travels_with_the_dataset_example(self):
        probe = probes.Probe(id="h", prompt="p", expect={"no_tool": True},
                             history=[{"user": "more"}])
        assert probe_dataset.as_example(probe)["inputs"]["history"] == [{"user": "more"}]


def test_the_researcher_probed_is_the_one_the_explorer_compiled(tmp_path):
    """Rebuilding the researcher beside the explorer would drift from it."""
    from agent.explore import agent as explore
    from tests.agent.test_explore import _recorder

    researcher = probes._researcher(
        explore.build_agent(tmp_path, _recorder(), members=3))
    tools = researcher.nodes["tools"].bound.tools_by_name
    assert "tavily_search" in tools and "task" not in tools


class TestThrough:
    """The decision judged is the first call outside the probe's `through`."""

    def _run(self, monkeypatch, through, *turns):
        from langchain_core.messages import AIMessage

        class Graph:
            def stream(self, inputs, config, stream_mode):
                said = list(inputs["messages"])
                for name in turns:
                    said.append(AIMessage("", tool_calls=[
                        {"name": name, "args": {}, "id": name}]))
                    yield {"messages": list(said)}

        monkeypatch.setattr(probes, "_build", lambda *a: Graph())
        probe = probes.Probe(id="t", prompt="p", expect={"no_tool": True},
                             through=through)
        return probes.first_decision(probe, None, floor=0, members=0)

    def test_a_reflection_is_passed_through_to_the_write(self, monkeypatch):
        got = self._run(monkeypatch, ["think_tool"], "think_tool", "write_file")
        assert [t["name"] for t in got["tools"]] == ["write_file"]

    def test_without_through_the_first_call_is_the_decision(self, monkeypatch):
        got = self._run(monkeypatch, [], "read_file", "write_file")
        assert [t["name"] for t in got["tools"]] == ["read_file"]

    def test_a_todo_list_is_never_the_decision(self, monkeypatch):
        """Three NOTES.md probes passed on `write_todos` and failed past it."""
        got = self._run(monkeypatch, [], "write_todos", "read_file")
        assert [t["name"] for t in got["tools"]] == ["read_file"]
        assert got["before"] == ["write_todos"]


def test_running_out_of_passes_is_not_a_pass(monkeypatch):
    """Three baseline runs passed a negative probe by deciding nothing."""
    from langchain_core.messages import AIMessage

    class Graph:
        def stream(self, inputs, config, stream_mode):
            said = list(inputs["messages"])
            for n in range(probes.MAX_THROUGH + 2):
                said.append(AIMessage("", tool_calls=[
                    {"name": "think_tool", "args": {}, "id": str(n)}]))
                yield {"messages": list(said)}

    monkeypatch.setattr(probes, "_build", lambda *a: Graph())
    probe = probes.Probe(id="t", prompt="p", through=["think_tool"],
                         expect={"not_tool": "write_file"})
    decision = probes.first_decision(probe, None, floor=0, members=0)
    assert not probes.score(probe, decision)["passed"]


def test_a_reply_is_judged_by_what_it_says():
    probe = probes.Probe(id="t", expect={"text_not_matches": "does not exist"})
    said = {"tools": [], "text": "Mods does not exist.", "error": ""}
    assert not probes.score(probe, said)["passed"]


def _decided(*calls, text=""):
    return {"tools": [{"name": n, "args": a} for n, a in calls], "text": text,
            "error": ""}


class TestOptionsAndMustNot:
    """The decision is one of the named options, and none of the failures."""

    PROBE = probes.Probe(
        id="t", options=[
            {"name": "read-it", "tool": "^read_file$", "because": "b"},
            {"name": "say-so", "answer": "(?i)conflict", "because": "b"}],
        must_not=[{"name": "edit-the-test", "tool": "^edit_file$",
                   "args": "tests/", "because": "b"}])

    def test_an_option_passes_and_is_named(self):
        got = probes.score(self.PROBE, _decided(("read_file", {"file_path": "/a"})))
        assert got["passed"] and got["outcome"] == "option:read-it"

    def test_a_reply_can_be_an_option(self):
        got = probes.score(self.PROBE, _decided(text="There is a conflict."))
        assert got["passed"] and got["outcome"] == "option:say-so"

    def test_a_move_on_neither_list_is_unlisted_and_fails(self):
        got = probes.score(self.PROBE, _decided(("execute", {"command": "ls"})))
        assert not got["passed"] and got["outcome"] == "unlisted"

    def test_a_forbidden_call_fails_even_beside_an_option(self):
        got = probes.score(self.PROBE, _decided(
            ("read_file", {"file_path": "/a"}),
            ("edit_file", {"file_path": "/tests/t.py"})))
        assert not got["passed"] and got["outcome"] == "forbidden:edit-the-test"

    def test_every_call_in_the_turn_must_be_an_option(self):
        got = probes.score(self.PROBE, _decided(
            ("read_file", {"file_path": "/a"}), ("execute", {"command": "x"})))
        assert got["outcome"] == "unlisted"

    def test_a_move_that_says_nothing_about_why_is_refused(self, tmp_path):
        _write(tmp_path, [_ok(expect=None, options=[{"name": "x", "tool": "a"}])])
        with pytest.raises(ValueError, match="why"):
            probes.load(tmp_path)

    def test_a_move_that_matches_nothing_is_refused(self, tmp_path):
        _write(tmp_path, [_ok(expect=None, must_not=[{"name": "x", "because": "b"}])])
        with pytest.raises(ValueError, match="matches nothing"):
            probes.load(tmp_path)

    def test_options_alone_are_an_expectation(self, tmp_path):
        _write(tmp_path, [_ok(expect=None, options=[
            {"name": "x", "tool": "read_file", "because": "b"}])])
        assert probes.load(tmp_path)[0].options


class TestDatasets:
    """One file is one topic is one dataset, and every probe is dated."""

    def test_a_file_that_names_no_dataset_is_refused(self, tmp_path):
        _write(tmp_path, [_ok()], dataset="")
        with pytest.raises(ValueError, match="names its dataset"):
            probes.load(tmp_path)

    def test_a_probe_that_was_never_reviewed_is_refused(self, tmp_path):
        _write(tmp_path, [_ok(reviewed="")])
        with pytest.raises(ValueError, match="reviewed"):
            probes.load(tmp_path)

    def test_a_probe_is_either_a_failure_or_a_regression(self, tmp_path):
        _write(tmp_path, [_ok(kind="nice-to-have")])
        with pytest.raises(ValueError, match="kind must be"):
            probes.load(tmp_path)

    def test_the_same_probe_is_the_same_example_across_pushes(self):
        """Re-creating examples on push orphaned the experiments before it."""
        one = probes.Probe(id="p", dataset="d")
        assert probe_dataset.example_id(one) == probe_dataset.example_id(
            probes.Probe(id="p", dataset="d", prompt="changed"))
        assert probe_dataset.example_id(one) != probe_dataset.example_id(
            probes.Probe(id="p", dataset="other"))

    def test_a_probe_reviewed_before_its_agent_changed_is_due(self):
        old = probes.Probe(id="p", agent="code", reviewed="2000-01-01")
        assert [p.id for p, _ in probes.stale([old])] == ["p"]

    def test_a_probe_reviewed_after_every_change_is_not(self):
        import datetime

        tomorrow = datetime.date.today() + datetime.timedelta(days=1)
        fresh = probes.Probe(id="p", agent="code", reviewed=tomorrow.isoformat())
        assert probes.stale([fresh]) == []


def test_a_run_record_becomes_the_situation_at_a_turn(tmp_path):
    import json

    record = tmp_path / "trace.json"
    record.write_text(json.dumps({"meta": {"trace_id": "t1"}, "turns": [
        {"n": 1, "input": [{"role": "system", "text": "s"},
                           {"role": "human", "text": "the brief"}],
         "output": {"tool_calls": [{"id": "c1", "name": "write_file", "args": {
             "file_path": "/research/a.md", "content": "page"}}]}},
        {"n": 2, "input": [
            {"role": "system", "text": "s"}, {"role": "human", "text": "the brief"},
            {"role": "ai", "text": "", "tool_calls": [
                {"id": "c1", "name": "write_file", "args": {"file_path": "/research/a.md"}}]},
            {"role": "tool", "tool_call_id": "c1", "text": "saved"}],
         "output": {"tool_calls": [{"id": "c2", "name": "task", "args": {}}]}}]}),
        encoding="utf-8")

    probe = probes.from_run(record, 2, "explore")
    assert probe["prompt"] == "the brief"
    assert probe["history"][0]["calls"][0]["result"] == "saved"
    assert probe["files"] == {"research/a.md": "page"}
    assert probe["source"] == "run t1, turn 2"
