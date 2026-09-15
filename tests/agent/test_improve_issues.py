"""The ledger, and the one rule that makes it worth keeping.

An issue closes when the runs recorded *after* its fix stop matching, and only
then. Everything here exists to stop the two ways that can go wrong silently:

- **Closing because nobody looked.** With no run recorded after the fix there is
  no evidence either way, and a store that read "zero matches" as "fixed" would
  retire real failures at exactly the moment nothing was being measured.
- **Losing the fact that it came back.** A reopened issue is the most
  informative row in the ledger, so status moves through an append-only history
  and an amend can never drop evidence a previous pass recorded.
"""

import json
from pathlib import Path

from agent.improve import issues, records


def _record(root: Path, name: str, verdict: dict) -> None:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "run.json").write_text(json.dumps(verdict), encoding="utf-8")


def _records(root: Path) -> list:
    return records.discover([root])


def _store(tmp_path) -> issues.IssueStore:
    return issues.IssueStore(tmp_path)


def test_upsert_creates_then_amends_without_losing_evidence(tmp_path):
    store = _store(tmp_path)
    issues.upsert(store, {"title": "Edits before reading",
                          "evidence": ["run-a"], "lever": "system_prompt.md"})
    issue = issues.upsert(store, {"id": "edits-before-reading",
                                  "evidence": ["run-b"], "severity": "high"})

    assert issue.evidence == ["run-a", "run-b"], "an amend merges, never replaces"
    assert issue.lever == "system_prompt.md", "an unnamed field is left alone"
    assert issue.severity == "high"
    assert [h["event"] for h in issue.history] == ["opened", "amended"]


def test_a_title_becomes_a_readable_id(tmp_path):
    issue = issues.upsert(_store(tmp_path), {"title": "`stopping` hides a hung call!"})
    assert issue.id == "stopping-hides-a-hung-call"


def test_an_unknown_status_is_refused_rather_than_stored(tmp_path):
    store = _store(tmp_path)
    issues.upsert(store, {"title": "T"})
    try:
        issues.upsert(store, {"id": "t", "status": "probably-fine"})
        raise AssertionError("should have refused")
    except ValueError as exc:
        assert "status must be one of" in str(exc)


def test_a_status_change_leaves_a_history_line(tmp_path):
    store = _store(tmp_path)
    issues.upsert(store, {"title": "T"})
    issue = issues.upsert(store, {"id": "t", "status": issues.FIXING})
    assert {"ts", "event", "detail"} <= set(issue.history[-1])
    assert "open -> fixing" in [h["detail"] for h in issue.history]


class TestCheck:
    """`check` is the only thing in this project that can close an issue."""

    def _issue(self, status=issues.FIXING, evidence=None):
        return issues.Issue(id="t", title="T", status=status,
                            evidence=evidence if evidence is not None else ["20260101T000000Z_before"],
                            signature={"where": {"failure_class": "stopping"}})

    def test_no_run_since_the_fix_leaves_the_status_alone(self, tmp_path):
        _record(tmp_path, "20260101T000000Z_before",
                {"failure_class": "stopping"})
        issue = self._issue()

        report = issues.check(issue, _records(tmp_path), since="2026-06-01")

        assert report["considered"] == 0
        assert issue.status == issues.FIXING, "unverified is not fixed"

    def test_a_clean_run_after_the_fix_closes_it(self, tmp_path):
        _record(tmp_path, "20260101T000000Z_before", {"failure_class": "stopping"})
        _record(tmp_path, "20260901T000000Z_after",
                {"failure_class": "", "scenario": "retry-after-case", "verified": True})
        _record(tmp_path, "20260902T000000Z_after_holdout",
                {"failure_class": "", "scenario": "stock-export", "verified": True})
        issue = self._issue()

        report = issues.check(issue, _records(tmp_path), since="2026-06-01")

        assert (report["considered"], report["matched"]) == (2, 0)
        assert issue.status == issues.CLOSED

    def test_the_failure_coming_back_reopens_a_closed_issue(self, tmp_path):
        _record(tmp_path, "20260901T000000Z_after", {"failure_class": "stopping"})
        issue = self._issue(status=issues.CLOSED)

        report = issues.check(issue, _records(tmp_path), since="2026-06-01")

        assert report["matched"] == 1
        assert issue.status == issues.REOPENED
        assert report["matching_runs"] == ["20260901T000000Z_after"]

    def test_a_still_failing_fix_is_not_marked_closed(self, tmp_path):
        _record(tmp_path, "20260901T000000Z_after", {"failure_class": "stopping"})
        issue = self._issue()

        issues.check(issue, _records(tmp_path), since="2026-06-01")

        assert issue.status == issues.FIXING

    def test_every_check_is_kept_on_the_issue(self, tmp_path):
        _record(tmp_path, "20260901T000000Z_after", {"failure_class": ""})
        issue = self._issue()

        issues.check(issue, _records(tmp_path), since="2026-06-01")
        issues.check(issue, _records(tmp_path), since="2026-06-01")

        assert len(issue.checks) == 2, "a check is evidence, not a status update"

    def test_never_matched_issue_does_not_close(self, tmp_path):
        _record(tmp_path, "20260901T000000Z_after", {"failure_class": ""})
        issue = self._issue(evidence=[])

        report = issues.check(issue, _records(tmp_path), since="2026-06-01")

        assert (report["considered"], report["matched"]) == (1, 0)
        assert report["verdict"] == "unproven"
        assert issue.status == issues.FIXING

    def test_missing_holdout_run_does_not_close(self, tmp_path):
        _record(tmp_path, "20260101T000000Z_before", {"failure_class": "stopping"})
        _record(tmp_path, "20260901T000000Z_after",
                {"failure_class": "", "scenario": "retry-after-case", "verified": True})
        issue = self._issue()

        report = issues.check(issue, _records(tmp_path), since="2026-06-01")

        assert issue.status == issues.FIXING


def test_the_ledger_survives_a_round_trip(tmp_path):
    store = _store(tmp_path)
    issues.upsert(store, {"title": "T", "signature": {"grep": "x"},
                          "evidence": ["r1", "r2"]})
    reloaded = issues.IssueStore(tmp_path).get("t")

    assert reloaded is not None
    assert reloaded.signature == {"grep": "x"}
    assert reloaded.evidence == ["r1", "r2"]


def test_an_unknown_field_on_disk_does_not_break_the_load(tmp_path):
    """The ledger outlives the code that wrote it, so a field added and later
    dropped must not make an old issue unreadable."""
    store = _store(tmp_path)
    issues.upsert(store, {"title": "T"})
    path = store.path("t")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["something_a_later_version_added"] = 1
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert store.get("t") is not None
