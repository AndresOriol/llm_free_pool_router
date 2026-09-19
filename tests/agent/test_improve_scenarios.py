"""Drafting an eval scenario out of a run that failed.

The set is what bounds every claim this loop can make — including the loop's
claims about its own fixes — so a drafting step that produces confident,
unusable briefs is worse than none. Two things are therefore checked here.

**A draft that could not become a scenario is refused.** No prompt is a topic,
not a task; no `fail_to_pass` is a scenario that cannot decide anything. Those
are the two fields the acceptance contract is built from
([`scenario.yaml`](../../docs/evaluation/scenarios.md#scenarioyaml)).

**Provenance is read, never claimed.** The most forgeable line in a brief is
"this really happened", so the outcome, the failure class and the files the run
touched come off `run.json` rather than out of the model's argument list.
"""

import json
from pathlib import Path

import pytest

from agent.improve import records, scenarios, tools


def _run(root: Path, name: str, verdict: dict) -> None:
    directory = root / "evals" / "results" / "runs" / name
    directory.mkdir(parents=True)
    (directory / "run.json").write_text(json.dumps(verdict), encoding="utf-8")


def _fields(**over) -> dict:
    base = {"title": "Case-insensitive lookup missed",
            "prompt": "The alert never fires for HEADER in caps. Fix it.",
            "fail_to_pass": "evaluation/tests/test_rules.py::test_upper_case"}
    base.update(over)
    return base


def test_a_draft_without_a_prompt_is_refused(tmp_path):
    store = scenarios.DraftStore(tmp_path)
    with pytest.raises(ValueError, match="prompt"):
        scenarios.draft(store, _fields(prompt=""))
    assert store.list() == [], "nothing was written"


def test_a_draft_that_decides_nothing_is_refused(tmp_path):
    store = scenarios.DraftStore(tmp_path)
    with pytest.raises(ValueError, match="fail_to_pass"):
        scenarios.draft(store, _fields(fail_to_pass=""))


def test_a_category_the_runner_would_reject_is_refused(tmp_path):
    store = scenarios.DraftStore(tmp_path)
    with pytest.raises(ValueError, match="category"):
        scenarios.draft(store, _fields(category="hard"))
    with pytest.raises(ValueError, match="difficulty"):
        scenarios.draft(store, _fields(difficulty="L9"))


def test_provenance_comes_off_the_run_not_the_argument(tmp_path):
    _run(tmp_path, "20260907T000000Z_r", {
        "outcome": "fail", "failure_class": "reasoning", "config": "code",
        "f2p_passed": 4, "f2p_total": 22, "diff_files": ["alerts/rules.py"]})
    record = records.discover([tmp_path / "evals" / "results" / "runs"])[0]

    made = scenarios.draft(scenarios.DraftStore(tmp_path), _fields(), record)
    page = made.render()

    assert "`fail`" in page and "`reasoning`" in page
    assert "4/22 fail_to_pass" in page
    assert "`alerts/rules.py`" in page
    assert made.source_run == "20260907T000000Z_r"


def test_the_brief_carries_the_four_headings_the_set_uses(tmp_path):
    page = scenarios.draft(scenarios.DraftStore(tmp_path), _fields()).render()

    for heading in ("## The seed", "## The task", "## The challenge",
                    "## What it checks"):
        assert heading in page
    assert "python -m evals validate" in page, "the gate is named"
    assert "scores every configuration far too well" in page, "and why"


def test_an_incomplete_draft_says_so_rather_than_reading_as_finished(tmp_path):
    page = scenarios.draft(scenarios.DraftStore(tmp_path),
                           _fields(pass_to_pass="")).render()
    assert "*(not stated)*" in page


class TestTheTool:
    def test_it_refuses_a_source_run_that_does_not_exist(self, tmp_path):
        made = tools.make_tools(tmp_path, None)
        answer = made["draft_scenario"].func(
            title="T", prompt="p", fail_to_pass="t", source_run="nope")

        assert answer.startswith("error:") and "provenance" in answer
        assert scenarios.DraftStore(tmp_path).list() == []

    def test_it_does_not_build_unless_asked(self, tmp_path):
        made = tools.make_tools(tmp_path, None)
        answer = made["draft_scenario"].func(**_fields())

        assert "Nothing has been built" in answer
        assert scenarios.DraftStore(tmp_path).list() == [
            "case-insensitive-lookup-missed"]

    def test_asking_to_build_without_a_scenario_repo_says_why(self, tmp_path):
        made = tools.make_tools(tmp_path, None)
        answer = made["draft_scenario"].func(build="yes", **_fields())

        assert "cannot be built from here" in answer
        # The draft still lands: the work of describing it is not lost.
        assert scenarios.DraftStore(tmp_path).list()

    def test_a_bad_draft_reports_rather_than_raising(self, tmp_path):
        made = tools.make_tools(tmp_path, None)
        answer = made["draft_scenario"].func(
            title="T", prompt="", fail_to_pass="t")

        assert answer.startswith("error:") and "Nothing written" in answer
