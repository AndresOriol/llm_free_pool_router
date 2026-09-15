import pytest
from pathlib import Path
from agent.improve.issues import Issue, check
from agent.improve.records import Record

def test_check_issue_gates():
    issue = Issue(id="test-issue", title="Test")
    issue.signature = {"kind": "eval", "where": {"outcome": "fail"}}
    issue.baseline_solved_train = 5
    issue.baseline_solved_holdout = 3

    # Mock records
    r1 = Record(
        id="r1", kind="eval", ts="2026-09-15T12:00:00Z", path=Path("/tmp"),
        verdict={"run_id": "r1", "scenario": "retry-after-case", "outcome": "pass", "verified": True, "config": "code"}
    )
    r2 = Record(
        id="r2", kind="eval", ts="2026-09-15T12:01:00Z", path=Path("/tmp"),
        verdict={"run_id": "r2", "scenario": "stock-export", "outcome": "pass", "verified": True, "config": "code"}
    )

    # Test missing holdout run
    issue.evidence = ["r1"] # so it's not unproven
    rep = check(issue, [r1], since="2026-09-15T10:00:00Z")
    assert issue.status == "open"
    assert rep["verdict"] == "open"

    # Test with both splits present and not regressed (baseline combined 8, current 2) -> wait, combined solved is 2 < 8, so regressed!
    # Let's set baselines to 1, 1 (combined 2)
    issue.baseline_solved_train = 1
    issue.baseline_solved_holdout = 1
    rep2 = check(issue, [r1, r2], since="2026-09-15T10:00:00Z")
    assert issue.status == "closed"
    assert rep2["verdict"] == "closed"
