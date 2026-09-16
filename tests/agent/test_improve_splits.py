"""Tests for split-aware issue gating and verification."""

import pytest
from pathlib import Path
from agent.improve.issues import Issue, check, OPEN, CLOSED, IssueStore
from agent.improve.tools import make_tools
from agent.improve.records import Record


def _record(tmp_path: Path, run_id: str, verdict: dict) -> Record:
    r_dir = tmp_path / "evals" / "results" / "runs" / run_id
    r_dir.mkdir(parents=True, exist_ok=True)
    r_json = r_dir / "run.json"
    import json
    r_json.write_text(json.dumps(verdict))
    return Record(id=run_id, kind="eval", ts="2026-09-15T12:00:00Z", path=r_dir, verdict=verdict)


def test_issue_does_not_close_when_holdout_run_is_missing(tmp_path):
    issue = Issue(id="test-issue", title="Test")
    issue.signature = {"kind": "eval", "where": {"outcome": "fail"}}
    issue.evidence = ["r1"]
    issue.status = OPEN
    issue.baseline = {
        "train": {"solved": 1, "runs": 1},
        "holdout": {"solved": 1, "runs": 1},
        "combined": {"solved": 2, "runs": 2}
    }

    r1 = _record(tmp_path, "r1", {"scenario": "retry-after-case", "verified": True})
    
    report = check(issue, [r1], since="2026-09-15T10:00:00Z")
    assert issue.status == OPEN
    assert not report["has_post_fix_holdout"]


def test_issue_does_not_close_on_combined_regression(tmp_path):
    issue = Issue(id="test-issue", title="Test")
    issue.signature = {"kind": "eval", "where": {"outcome": "fail"}}
    issue.evidence = ["r1"]
    issue.status = OPEN
    issue.baseline = {
        "train": {"solved": 5, "runs": 5},
        "holdout": {"solved": 3, "runs": 3},
        "combined": {"solved": 8, "runs": 8}
    }

    r1 = _record(tmp_path, "r1", {"scenario": "retry-after-case", "verified": True})
    r2 = _record(tmp_path, "r2", {"scenario": "stock-export", "verified": True})

    report = check(issue, [r1, r2], since="2026-09-15T10:00:00Z")
    assert issue.status == OPEN
    assert report["regressed"] is True


def test_issue_closes_when_score_holds_or_improves(tmp_path):
    issue = Issue(id="test-issue", title="Test")
    issue.signature = {"kind": "eval", "where": {"outcome": "fail"}}
    issue.evidence = ["r1"]
    issue.status = OPEN
    issue.baseline = {
        "train": {"solved": 1, "runs": 1},
        "holdout": {"solved": 1, "runs": 1},
        "combined": {"solved": 2, "runs": 2}
    }

    r1 = _record(tmp_path, "r1", {"scenario": "retry-after-case", "verified": True})
    r2 = _record(tmp_path, "r2", {"scenario": "stock-export", "verified": True})

    report = check(issue, [r1, r2], since="2026-09-15T10:00:00Z")
    assert issue.status == CLOSED
    assert report["verdict"] == CLOSED


def test_issue_does_not_close_without_baseline(tmp_path):
    issue = Issue(id="test-issue", title="Test")
    issue.signature = {"kind": "eval", "where": {"outcome": "fail"}}
    issue.evidence = ["r1"]
    issue.status = OPEN
    issue.baseline = {}

    r1 = _record(tmp_path, "r1", {"scenario": "retry-after-case", "verified": True})
    r2 = _record(tmp_path, "r2", {"scenario": "stock-export", "verified": True})

    report = check(issue, [r1, r2], since="2026-09-15T10:00:00Z")
    assert issue.status == OPEN
    assert not report["has_baseline"]


def test_check_issue_tool_regression_message(tmp_path):
    tools = make_tools(tmp_path)
    store = IssueStore(tmp_path)
    issue = Issue(id="reg-issue", title="Regression Test")
    issue.signature = {"kind": "eval"}
    issue.evidence = ["r1"]
    issue.status = OPEN
    issue.baseline = {
        "train": {"solved": 5, "runs": 5},
        "holdout": {"solved": 3, "runs": 3},
        "combined": {"solved": 8, "runs": 8}
    }
    store.save(issue)

    _record(tmp_path, "r1", {"scenario": "retry-after-case", "verified": True})
    _record(tmp_path, "r2", {"scenario": "stock-export", "verified": True})

    check_tool = tools["check_issue"]
    res = check_tool.func("reg-issue", since="2026-09-15T10:00:00Z")
    assert "regressed below baseline" in res
    assert "vs baseline 8" in res

