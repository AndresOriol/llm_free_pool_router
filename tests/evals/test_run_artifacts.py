"""A run's artifacts are its only record, so they have to be readable back.

The workdir is a temp directory that no longer exists by the time anything
reads a run, which makes `diff.patch` the sole account of what the agent did:
`metrics` counts it, the failure taxonomy derives `touched` from it, and
`bundle` hands it to a reviewer. It was being written twice-corrupted, and both
corruptions are invisible -- the file still looks like a diff.
"""

import shutil
import subprocess
from pathlib import Path

from evals import run as run_mod, verify

EM_DASH = "The rule -- written with an em dash — is the one that matters.\n"


def _pair(tmp_path: Path) -> tuple:
    seed, work = tmp_path / "seed", tmp_path / "work"
    for root in (seed, work):
        root.mkdir()
        (root / "NOTES.md").write_text(EM_DASH, encoding="utf-8")
    (work / "NOTES.md").write_text(EM_DASH + "— and a second one.\n",
                                   encoding="utf-8")
    return seed, work


def test_the_diff_survives_a_non_ascii_seed(tmp_path):
    """`text=True` decodes with the locale codepage, which on Windows is cp1252.

    The first full-set batch shows the cost: every em dash in a seed's prose
    reached `diff.patch` as `â€"`, three characters, in all ten runs.
    """
    seed, work = _pair(tmp_path)

    patch = verify.make_diff(seed, work)
    assert "—" in patch
    assert "â€" not in patch


def test_a_recorded_patch_applies_back_to_its_seed(tmp_path):
    """The property both fixes exist for: a run can be replayed.

    A patch is only a record of what the agent did if it can be put back. This
    fails on the encoding above, and it fails again on the line endings --
    `write_text` translates each newline to a CRLF pair on Windows, and
    `git apply` refuses context lines carrying a CR the target does not have.
    """
    seed, work = _pair(tmp_path)

    recorded = tmp_path / "diff.patch"
    run_mod._write(recorded, verify.make_diff(seed, work))
    assert b"\r\n" not in recorded.read_bytes()

    replay = tmp_path / "replay"
    shutil.copytree(seed, replay)
    applied = subprocess.run(["git", "apply", "-p1", "-"], cwd=replay,
                             input=recorded.read_bytes(), capture_output=True)
    assert applied.returncode == 0, applied.stderr.decode(errors="replace")
    assert (replay / "NOTES.md").read_text(encoding="utf-8") == \
        (work / "NOTES.md").read_text(encoding="utf-8")


def test_pytest_output_is_not_mangled_by_the_locale(tmp_path):
    """`verify.txt` is the evidence for a disputed f2p result."""
    (tmp_path / "test_dash.py").write_text(
        'def test_it():\n    assert "—" == "-", "the rule — as written"\n',
        encoding="utf-8")

    _, log = verify._pytest("test_dash.py", tmp_path)
    assert "â€" not in log


ACCOUNT_DIFF = """diff --git a/NOTES.md b/NOTES.md
--- a/NOTES.md
+++ b/NOTES.md
@@ -1,3 +1,5 @@
 # Notes
+
+## 2026-09-05 — widened the suite
 
 Working journal.
diff --git a/durations/parse.py b/durations/parse.py
--- a/durations/parse.py
+++ b/durations/parse.py
@@ -1,2 +1,3 @@
 def parse(text):
+    text = text.strip()
     return text
"""


def test_an_account_is_lines_added_to_the_feedback_file(tmp_path):
    """R7: the session reads the project's notes and appends its own account."""
    from evals import metrics

    assert metrics.account_written(ACCOUNT_DIFF) is True
    assert metrics.added_by_file(ACCOUNT_DIFF) == {
        "NOTES.md": 2, "durations/parse.py": 1}


def test_working_everywhere_but_the_notes_is_not_an_account(tmp_path):
    """The silent-competence case: 26 of 40 recorded runs did exactly this."""
    from evals import metrics

    code_only = ACCOUNT_DIFF.split("diff --git a/durations")[1]
    assert metrics.account_written("diff --git a/durations" + code_only) is False


def test_deleting_the_notes_is_not_writing_an_account(tmp_path):
    """`+++ /dev/null` never opens a counter, so a deletion contributes nothing."""
    from evals import metrics

    deletion = ("diff --git a/NOTES.md b/NOTES.md\n"
                "--- a/NOTES.md\n+++ /dev/null\n@@ -1,2 +0,0 @@\n"
                "-# Notes\n-Working journal.\n")
    assert metrics.account_written(deletion) is False


TEST_DIFF = """diff --git a/tests/test_bots.py b/tests/test_bots.py
--- a/tests/test_bots.py
+++ b/tests/test_bots.py
@@ -10,3 +10,8 @@ def test_existing():
     assert True
+
+
+def test_cautious_tie_break_high():
+    assert engine.play("cautious", FLAT, 1) == [(2, 3)]
diff --git a/bots/engine.py b/bots/engine.py
--- a/bots/engine.py
+++ b/bots/engine.py
@@ -1,2 +1,3 @@
 def play(name):
+    # def test_not_really(): this is a comment, not a test
     return name
"""


def test_an_added_test_is_counted(tmp_path):
    """The behaviour a standing maintainer most needs, previously only penalised."""
    from evals import metrics

    assert metrics.added_tests(TEST_DIFF) == 1


def test_a_def_outside_a_test_file_is_not_a_test(tmp_path):
    """`bots/engine.py` is not collected, so nothing in it can be a new test."""
    from evals import metrics

    module_only = TEST_DIFF.split("diff --git a/bots")[1]
    assert metrics.added_tests("diff --git a/bots" + module_only) == 0


def test_removing_a_test_does_not_count_as_adding_one(tmp_path):
    from evals import metrics

    removal = ("diff --git a/tests/test_bots.py b/tests/test_bots.py\n"
               "--- a/tests/test_bots.py\n+++ b/tests/test_bots.py\n@@ -1,4 +1,1 @@\n"
               "-def test_gone():\n-    assert True\n")
    assert metrics.added_tests(removal) == 0


def test_a_run_that_edited_nothing_is_recorded_as_such():
    """The mechanical half of a failure the harness cannot fully see.

    Three recorded runs finished with an empty tree and a closing message
    reporting the work as done. Whether prose claims completion needs a reader;
    whether an edit tool was called does not.
    """
    from evals import metrics

    events = [
        {"event": "tool_start", "tool": "read_file"},
        {"event": "tool_start", "tool": "execute"},
    ]
    assert metrics.from_trace(events)["edited_nothing"] is True

    events.append({"event": "tool_start", "tool": "edit_file"})
    assert metrics.from_trace(events)["edited_nothing"] is False
