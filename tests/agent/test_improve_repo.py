"""The rails that make an unattended pass safe to leave running.

This agent modifies the harness it is running on, so two things that would be
tidiness elsewhere are load-bearing here.

**A fix never lands on the branch the pass started on.** The first live pass
delegated on `master`; the only reason `master` was not written to is that the
session made no change, and nothing prevented it.

**"I could not look" is never reported as "this broke".** The suite gate exists
to stop a change that breaks the tests from counting as a fix -- and a gate that
cannot tell a failing suite from a missing one would block every fix it could
not measure instead. pytest exit 4 (no such path) was being read as failure.
"""

import subprocess
from pathlib import Path

from agent.improve import repo


def _repo(tmp_path: Path) -> Path:
    run = lambda *a: subprocess.run(("git", *a), cwd=str(tmp_path),
                                    capture_output=True, check=True)
    run("init", "-q", "-b", "master")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    run("add", "-A")
    run("commit", "-qm", "first")
    return tmp_path


def test_a_branch_is_named_after_its_issue(tmp_path):
    assert repo.branch_name("stale-diagnosis") == "improve/stale-diagnosis"
    # An id that would not be a legal ref must not produce a broken command.
    assert " " not in repo.branch_name("a b c")
    assert repo.branch_name("..").startswith("improve/")


def test_switching_creates_then_reuses_one_branch_per_issue(tmp_path):
    root = _repo(tmp_path)

    ok, note = repo.switch_to(root, "improve/t")
    assert ok and "new branch" in note
    assert repo.current_branch(root) == "improve/t"

    subprocess.run(("git", "checkout", "-q", "master"), cwd=str(root), check=True)
    ok, note = repo.switch_to(root, "improve/t")
    assert ok and "existing branch" in note, "a second attempt is the same issue"


def test_the_pass_is_put_back_on_the_branch_it_started_on(tmp_path):
    root = _repo(tmp_path)

    with repo.restored(root) as started:
        assert started == "master"
        repo.switch_to(root, "improve/t")
        assert repo.current_branch(root) == "improve/t"

    assert repo.current_branch(root) == "master"
    # The work is kept; only the checkout moved back.
    assert repo.git(root, "rev-parse", "--verify", "improve/t")[0]


def test_the_tree_is_restored_even_when_the_pass_raises(tmp_path):
    root = _repo(tmp_path)
    try:
        with repo.restored(root):
            repo.switch_to(root, "improve/t")
            raise RuntimeError("the pass died")
    except RuntimeError:
        pass
    assert repo.current_branch(root) == "master"


def test_a_directory_that_is_not_a_repository_is_not_an_error(tmp_path):
    assert not repo.is_repo(tmp_path)
    with repo.restored(tmp_path) as started:
        assert started is None


def test_uncommitted_work_is_never_discarded_to_tidy_up(tmp_path):
    """Leaving the tree on a fix branch is a mess someone can see. Throwing
    away an edit to avoid that mess is not recoverable."""
    root = _repo(tmp_path)
    with repo.restored(root):
        repo.switch_to(root, "improve/t")
        (root / "a.py").write_text("x = 2  # half-done\n", encoding="utf-8")

    assert "x = 2" in (root / "a.py").read_text(encoding="utf-8")
    assert repo.dirty(root) == ["a.py"]


class TestTheSuiteGate:
    def test_a_missing_suite_is_not_a_failing_suite(self, tmp_path):
        passed, note = repo.run_tests(tmp_path, timeout=120)
        assert passed is True
        assert "No suite was run" in note

    def test_a_passing_suite_passes(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_ok.py").write_text(
            "def test_ok():\n    assert True\n", encoding="utf-8")

        passed, _ = repo.run_tests(tmp_path, timeout=120)
        assert passed is True

    def test_a_failing_suite_fails(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_bad.py").write_text(
            "def test_bad():\n    assert False\n", encoding="utf-8")

        passed, tail = repo.run_tests(tmp_path, timeout=120)
        assert passed is False
        assert "failed" in tail.lower()
