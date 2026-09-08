"""The improvement agent's tools, without a pool and without a coding session.

The loop's integrity lives in three places and all three are asserted here.

- **A fix is always attached to an issue.** `delegate_fix` refuses an unknown
  one, because a change nobody can re-check afterwards is the failure this whole
  design exists to prevent.
- **Nothing spends quota by accident.** `run_evals` refuses an unscoped batch,
  which would otherwise be one tool call away from hours of free-tier requests.
- **A refusal is readable.** Every bad input comes back as text the model can
  correct from, never as an exception that ends the run -- the lesson already
  paid for by a session that spent 900 of 1,050 seconds on shell syntax nobody
  had told it was refused.
"""

import json
from pathlib import Path

from agent.improve import issues, tools
from agent.improve.readonly import ReadOnlyMiddleware
from agent.protocol import (AgentCard, AgentRegistry, AgentSkill, Artifact,
                            LocalTransport, Message, TaskState)

CODE_CARD = AgentCard(name="code", version="0.1.0",
                      description="Works a project.",
                      skills=[AgentSkill(id="work_project", name="Work",
                                         description="Change and commit.")])


def _run(root: Path, name: str, verdict: dict) -> None:
    directory = root / "evals" / "results" / "runs" / name
    directory.mkdir(parents=True)
    (directory / "run.json").write_text(json.dumps(verdict), encoding="utf-8")


def _transport(recorder=None):
    registry = AgentRegistry()

    def handler(task):
        if recorder is not None:
            recorder.append(task.history[-1].text)
        return task.advance(TaskState.COMPLETED, Message.agent("Committed."))

    registry.register(CODE_CARD, handler)
    return LocalTransport(registry)


def test_delegate_fix_is_absent_without_a_code_peer(tmp_path):
    """Diagnose-only is a supported mode, so the tool is absent rather than
    present and certain to refuse."""
    assert "delegate_fix" not in tools.make_tools(tmp_path, None)
    assert "delegate_fix" in tools.make_tools(tmp_path, _transport())


def test_write_issue_reports_bad_json_and_writes_nothing(tmp_path):
    made = tools.make_tools(tmp_path, None)

    answer = made["write_issue"].func(title="T", signature="{not json")

    assert answer.startswith("error:") and "Nothing written" in answer
    assert not issues.IssueStore(tmp_path).list()


def test_write_issue_records_the_signature_and_the_evidence(tmp_path):
    _run(tmp_path, "20260901T000000Z_a", {"failure_class": "stopping"})
    made = tools.make_tools(tmp_path, None)

    made["write_issue"].func(
        title="Stopping hides a hung call", lever="session.py",
        signature=json.dumps({"where": {"failure_class": "stopping"}}),
        evidence="20260901T000000Z_a, 20260902T000000Z_b")

    issue = issues.IssueStore(tmp_path).get("stopping-hides-a-hung-call")
    assert issue.evidence == ["20260901T000000Z_a", "20260902T000000Z_b"]
    assert issue.signature == {"where": {"failure_class": "stopping"}}
    # Dated from the runs that actually match, not from the ids the model listed.
    assert issue.last_seen.startswith("2026-09-01")


def test_delegate_fix_refuses_an_issue_that_does_not_exist(tmp_path):
    sent = []
    made = tools.make_tools(tmp_path, _transport(sent))

    answer = made["delegate_fix"].func(issue_id="nope", brief="Fix it.")

    assert answer.startswith("error:") and sent == [], "nothing was delegated"


def test_delegate_fix_records_the_task_and_moves_the_status(tmp_path):
    sent = []
    made = tools.make_tools(tmp_path, _transport(sent))
    made["write_issue"].func(title="T", signature='{"kind": "eval"}')

    answer = made["delegate_fix"].func(issue_id="t", brief="Raise the budget.")

    assert "Raise the budget." in sent[0]
    assert "issue `t`" in sent[0], "the delegate is told which issue this is"
    issue = issues.IssueStore(tmp_path).get("t")
    assert issue.status == issues.FIXING
    assert issue.tasks and issue.tasks[0]["agent"] == "code"
    assert "not fixed until" in answer


def test_check_issue_defaults_to_when_the_fix_was_delegated(tmp_path):
    """The only boundary that answers "did it stop happening"."""
    made = tools.make_tools(tmp_path, _transport())
    made["write_issue"].func(title="T",
                             signature='{"where": {"outcome": "fail"}}')
    made["delegate_fix"].func(issue_id="t", brief="b")

    answer = made["check_issue"].func(issue_id="t")

    assert "No run has been recorded since" in answer
    assert issues.IssueStore(tmp_path).get("t").status == issues.FIXING


def test_check_issue_refuses_to_replay_an_issue_with_no_signature(tmp_path):
    made = tools.make_tools(tmp_path, None)
    made["write_issue"].func(title="T")

    assert "no signature" in made["check_issue"].func(issue_id="t")


def test_check_issue_with_no_argument_lists_the_ledger(tmp_path):
    made = tools.make_tools(tmp_path, None)
    assert "ledger" in made["check_issue"].func().lower()
    made["write_issue"].func(title="Edits before reading")
    assert "edits-before-reading" in made["check_issue"].func()


def test_run_evals_refuses_an_unscoped_batch(tmp_path):
    """One tool call must not be able to start hours of free-tier requests."""
    answer = tools.make_tools(tmp_path, None)["run_evals"].func(config="code")
    assert answer.startswith("error:") and "scenario" in answer


def test_find_runs_reports_a_bad_regex_rather_than_raising(tmp_path):
    _run(tmp_path, "20260901T000000Z_a", {"outcome": "fail"})
    answer = tools.make_tools(tmp_path, None)["find_runs"].func(pattern="(unclosed")
    assert answer.startswith("error:") and "regex" in answer


def test_find_runs_filters_and_says_how_many_it_looked_at(tmp_path):
    _run(tmp_path, "20260901T000000Z_a", {"outcome": "fail", "config": "code"})
    _run(tmp_path, "20260902T000000Z_b", {"outcome": "pass", "config": "code"})
    made = tools.make_tools(tmp_path, None)

    assert "2 run(s)" in made["find_runs"].func()
    assert "20260902T000000Z_b" not in made["find_runs"].func(outcome="fail")
    assert "Nothing matched" in made["find_runs"].func(config="nope")


def test_read_run_names_the_run_it_could_not_find(tmp_path):
    answer = tools.make_tools(tmp_path, None)["read_run"].func(run_id="nope")
    assert "find_runs" in answer


def test_a_live_record_is_labelled_as_having_no_verdict(tmp_path):
    live = tmp_path / "live" / "20260901T000000Z_task"
    live.mkdir(parents=True)
    (live / "trace.json").write_text('{"turns": []}', encoding="utf-8")
    import os
    os.environ["IMPROVE_RECORDS"] = str(tmp_path / "live")
    try:
        answer = tools.make_tools(tmp_path, None)["read_run"].func(
            run_id="20260901T000000Z_task")
    finally:
        del os.environ["IMPROVE_RECORDS"]

    assert "no verdict" in answer, answer


class TestReadOnly:
    """The agent that diagnoses must not be the one that edits."""

    class _Request:
        def __init__(self, name):
            self.tool_call = {"name": name, "id": "1",
                              "args": {"file_path": "/agent/code/prompt.py"}}

    def test_a_write_comes_back_as_a_correctable_refusal(self):
        answer = ReadOnlyMiddleware().wrap_tool_call(
            self._Request("edit_file"), lambda r: "edited")

        assert answer.status == "error"
        assert "delegate_fix" in answer.content

    def test_everything_else_passes_through(self):
        assert ReadOnlyMiddleware().wrap_tool_call(
            self._Request("read_file"), lambda r: "contents") == "contents"


class TestStaleEvidence:
    """The guard the first live pass needed and did not have.

    That pass found two real `GraphRecursionError` crashes, cited them
    correctly, and diagnosed a 120-step limit in `agent/code/session.py` that
    had been 400 since the day before. The evidence was real and the conclusion
    was stale; a whole coding session was spent re-making a change that already
    existed. The difference between the two is one `git log` on the file the
    issue had already named as its lever.
    """

    def _repo(self, tmp_path, lever_body="LIMIT = 400\n"):
        """A tiny git repo with one run recorded before the lever changed."""
        import subprocess
        run = lambda *a: subprocess.run(("git", *a), cwd=str(tmp_path),
                                        capture_output=True, check=True)
        run("init", "-q")
        run("config", "user.email", "t@t")
        run("config", "user.name", "t")
        (tmp_path / "session.py").write_text(lever_body, encoding="utf-8")
        run("add", "session.py")
        run("commit", "-qm", "the fix that already shipped")
        # Recorded well before that commit, which is "now".
        _run(tmp_path, "20200101T000000Z_old", {"outcome": "fail"})
        return tmp_path

    def test_write_issue_warns_when_the_lever_outlives_the_evidence(self, tmp_path):
        made = tools.make_tools(self._repo(tmp_path), None)

        answer = made["write_issue"].func(
            title="T", lever="/session.py",
            signature=json.dumps({"where": {"outcome": "fail"}}))

        assert "may already be fixed" in answer
        assert "git log -p" in answer, "it names the command that would settle it"

    def test_delegate_fix_refuses_a_stale_issue(self, tmp_path):
        sent = []
        made = tools.make_tools(self._repo(tmp_path), _transport(sent))
        made["write_issue"].func(
            title="T", lever="/session.py",
            signature=json.dumps({"where": {"outcome": "fail"}}))

        answer = made["delegate_fix"].func(issue_id="t", brief="Raise it.")

        assert "Nothing was delegated" in answer
        assert sent == [], "no coding session was spent"
        assert issues.IssueStore(tmp_path).get("t").status == issues.OPEN

    def test_evidence_recorded_after_the_change_lets_it_through(self, tmp_path):
        """The way out is the correct behaviour: record a run against the code
        as it actually is, and if the failure survives, the issue is real."""
        root = self._repo(tmp_path)
        _run(root, "20990101T000000Z_fresh", {"outcome": "fail"})
        sent = []
        made = tools.make_tools(root, _transport(sent))
        made["write_issue"].func(
            title="T", lever="/session.py",
            signature=json.dumps({"where": {"outcome": "fail"}}))

        answer = made["delegate_fix"].func(issue_id="t", brief="Raise it.")

        assert "Nothing was delegated" not in answer
        assert sent, "the failure was seen against the current code"

    def test_an_unnamed_lever_cannot_be_checked_and_does_not_block(self, tmp_path):
        sent = []
        made = tools.make_tools(self._repo(tmp_path), _transport(sent))
        made["write_issue"].func(title="T", signature='{"kind": "eval"}')

        made["delegate_fix"].func(issue_id="t", brief="b")

        assert sent, "no lever means nothing to compare; it is not a refusal"

    def test_the_closing_message_is_not_taken_at_face_value(self, tmp_path):
        made = tools.make_tools(tmp_path, _transport())
        made["write_issue"].func(title="T", signature='{"kind": "eval"}')

        answer = made["delegate_fix"].func(issue_id="t", brief="b")

        assert "read the diff before you believe" in answer
        assert "not fixed until runs recorded after this" in answer


class TestStalenessUsesTheCommitNotTheClock:
    """An eval run pins a SHA, so *when* it ran says nothing about *what* it ran.

    A run started an hour after a fix landed still exercises the commit its
    configuration pinned. The first version of this check compared timestamps
    and reached the right verdict on the case it was written for by luck: git
    reported the lever's change in a +02:00 offset, which sorts after a UTC
    stamp that is actually later in real time.
    """

    def _repo(self, tmp_path):
        import subprocess
        run = lambda *a: subprocess.run(("git", *a), cwd=str(tmp_path),
                                        capture_output=True, check=True)
        sha = lambda: subprocess.run(("git", "rev-parse", "HEAD"),
                                     cwd=str(tmp_path), capture_output=True,
                                     encoding="utf-8", check=True).stdout.strip()
        run("init", "-q")
        run("config", "user.email", "t@t")
        run("config", "user.name", "t")
        (tmp_path / "other.py").write_text("x = 1\n", encoding="utf-8")
        run("add", "-A"); run("commit", "-qm", "before")
        before = sha()
        (tmp_path / "session.py").write_text("LIMIT = 400\n", encoding="utf-8")
        run("add", "-A"); run("commit", "-qm", "raise the limit")
        return before, sha()

    def _issue(self, root, name, config_sha):
        _run(root, name, {"outcome": "fail", "config_sha": config_sha})
        made = tools.make_tools(root, _transport())
        made["write_issue"].func(
            title="T", lever="/session.py",
            signature=json.dumps({"where": {"outcome": "fail"}}))
        return made

    def test_a_run_pinned_before_the_change_is_stale(self, tmp_path):
        before, _ = self._repo(tmp_path)
        made = self._issue(tmp_path, "29990101T000000Z_late_but_old", before)

        answer = made["delegate_fix"].func(issue_id="t", brief="Raise it.")

        # Dated in the far future, so a clock comparison would let it through.
        assert "Nothing was delegated" in answer
        assert "different version of the code" in answer

    def test_a_run_pinned_after_the_change_is_current(self, tmp_path):
        _, after = self._repo(tmp_path)
        made = self._issue(tmp_path, "20000101T000000Z_early_but_new", after)

        answer = made["delegate_fix"].func(issue_id="t", brief="Raise it.")

        # Dated in the distant past, so a clock comparison would refuse it.
        assert "Nothing was delegated" not in answer

    def test_an_unknown_sha_does_not_become_a_refusal(self, tmp_path):
        """An unanswerable question is not evidence of staleness."""
        self._repo(tmp_path)
        made = self._issue(tmp_path, "20260101T000000Z_r", "0" * 40)

        assert "Nothing was delegated" not in made["delegate_fix"].func(
            issue_id="t", brief="b")


def test_offsets_are_normalised_before_being_compared():
    from agent.improve.records import _utc
    assert _utc("2026-09-07T10:20:18+02:00") < _utc("2026-09-07T08:59:32+00:00")
    assert _utc("not a date") is None


class TestADelegationThatChangedNothing:
    """A delegation is believed by the repository, not by the delegate.

    The first live pass advanced its issue to `fixing` on a coding session that
    committed nothing and changed no file. The ledger then read as work under
    way, which is the state the whole design exists to keep honest.
    """

    def _transport_reporting(self, data):
        from agent.protocol.types import DataPart
        registry = AgentRegistry()

        def handler(task):
            task.artifacts.append(Artifact(name="workspace",
                                           parts=[DataPart(data)]))
            return task.advance(TaskState.COMPLETED,
                                Message.agent("Implemented all three changes."))

        registry.register(CODE_CARD, handler)
        return LocalTransport(registry)

    def _made(self, tmp_path, data):
        made = tools.make_tools(tmp_path, self._transport_reporting(data))
        made["write_issue"].func(title="T", signature='{"kind": "eval"}')
        return made

    def test_the_status_does_not_advance_when_nothing_moved(self, tmp_path):
        made = self._made(tmp_path, {"git": True, "branch": "master",
                                     "commits": 0, "files_changed": []})

        answer = made["delegate_fix"].func(issue_id="t", brief="Fix it.")

        assert "changed nothing" in answer
        assert "Do not delegate the same brief again" in answer
        issue = issues.IssueStore(tmp_path).get("t")
        assert issue.status == issues.OPEN, "not `fixing` — no work was done"
        assert issue.tasks[0]["changed_anything"] is False

    def test_the_status_advances_when_the_repository_moved(self, tmp_path):
        made = self._made(tmp_path, {"git": True, "branch": "fix/t",
                                     "commits": 1,
                                     "files_changed": ["agent/code/prompt.py"]})

        made["delegate_fix"].func(issue_id="t", brief="Fix it.")

        issue = issues.IssueStore(tmp_path).get("t")
        assert issue.status == issues.FIXING
        assert issue.tasks[0]["changed_anything"] is True

    def test_an_agent_that_reports_no_git_state_is_not_called_a_failure(self, tmp_path):
        made = tools.make_tools(tmp_path, _transport())
        made["write_issue"].func(title="T", signature='{"kind": "eval"}')

        made["delegate_fix"].func(issue_id="t", brief="Fix it.")

        assert issues.IssueStore(tmp_path).get("t").status == issues.FIXING


def test_delegate_fix_commits_ledger_before_switching(tmp_path):
    import subprocess
    from agent.improve import repo
    run = lambda *a: subprocess.run(("git", *a), cwd=str(tmp_path),
                                    capture_output=True, check=True)
    run("init", "-q", "-b", "master")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    run("add", "-A")
    run("commit", "-qm", "first")

    made = tools.make_tools(tmp_path, _transport())
    made["write_issue"].func(title="T", signature='{"kind": "eval"}')

    assert (tmp_path / "evals" / "results" / "issues" / "t.json").is_file()
    ok, out = repo.git(tmp_path, "status", "--porcelain")
    assert "evals/" in out

    made["delegate_fix"].func(issue_id="t", brief="Fix it.")

    done = subprocess.run(("git", "log", "master", "-1", "--pretty=%B"), cwd=str(tmp_path),
                          capture_output=True, encoding="utf-8", check=True)
    assert "Update ledger for issue t" in done.stdout


def test_delegate_fix_fails_when_ledger_commit_fails(tmp_path, monkeypatch):
    import subprocess
    from agent.improve import repo
    run = lambda *a: subprocess.run(("git", *a), cwd=str(tmp_path),
                                    capture_output=True, check=True)
    run("init", "-q", "-b", "master")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    run("add", "-A")
    run("commit", "-qm", "first")

    made = tools.make_tools(tmp_path, _transport())
    made["write_issue"].func(title="T", signature='{"kind": "eval"}')

    monkeypatch.setattr(repo, "commit_ledger", lambda w, i: (False, "mocked commit failure"))

    answer = made["delegate_fix"].func(issue_id="t", brief="Fix it.")

    # The prose is free to change; what must hold is that the tool refused,
    # said why, and left the checkout where it was. A delegation that went
    # ahead here would carry the diagnosis onto the fix branch again.
    assert answer.startswith("error:")
    assert "mocked commit failure" in answer
    assert repo.current_branch(tmp_path) == "master"
