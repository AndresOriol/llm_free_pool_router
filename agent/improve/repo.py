"""The repository as this loop touches it: a branch per fix, and a way back.

Three facts make unattended self-improvement different from an agent working
someone else's project, and all three are about *this* repository:

1. **The agent is modifying the harness it is running on.** A change that breaks
   `llm_router` or `agent/utils` does not fail a test somewhere; it stops the
   next pass from being able to run at all. That is the one failure mode with no
   recovery from inside the loop.
2. **A delegated coding session commits wherever it finds itself.** The first
   live pass ran on `master`, and the only reason `master` was not written to is
   that the session made no change. Nothing prevented it.
3. **Nobody is watching.** Whatever state the tree is left in is the state the
   next pass, the next eval batch and the next human all start from.

So: every fix goes on `improve/<issue-id>`, taken from the branch the pass
started on, and the pass puts the tree back where it found it before it ends.
`master` is never the branch a delegation runs on, and a broken branch is a
branch — not the checkout everything else uses.

**Nothing here merges.** Nothing stops it either -- `git` is unfiltered since
the harness moved to deepagents' own backend
([Why the restrictions went](../../docs/agents/code.md#why-the-restrictions-went)) -- so this is a
line the code keeps rather than one the harness enforces: a branch plus a ledger
entry is what a human reviews, and an unattended loop that merged its own work
would have no reviewer at all ([It cannot change the harness, and that is the point](../../docs/agents/improve.md#it-cannot-change-the-harness-and-that-is-the-point)).
"""

from __future__ import annotations

import logging
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

logger = logging.getLogger("harness.improve")

# Where a delegated fix lands. One branch per issue, so the ledger entry and the
# branch name are the same string and a reviewer needs no lookup table.
BRANCH_PREFIX = "improve/"

_TIMEOUT = 60
_SAFE = re.compile(r"[^A-Za-z0-9._/-]+")


def git(workdir: Path, *args: str) -> tuple:
    """(ok, output) for one git command. Never raises."""
    try:
        done = subprocess.run(("git", *args), cwd=str(workdir),
                              capture_output=True, encoding="utf-8",
                              errors="replace", timeout=_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{exc!r}"
    # `rstrip`, never `strip`. `git status --porcelain` puts the status in the
    # first two columns, so its first line begins with a space for an unstaged
    # change -- and stripping that shifted every offset by one, turning `a.py`
    # into `.py` in the only list anyone reads to see what a run left behind.
    return done.returncode == 0, (done.stdout or done.stderr or "").rstrip()


def branch_name(issue_id: str) -> str:
    return BRANCH_PREFIX + _SAFE.sub("-", issue_id).strip("-/.") or "issue"


def current_branch(workdir: Path) -> Optional[str]:
    ok, name = git(workdir, "rev-parse", "--abbrev-ref", "HEAD")
    return name if ok and name and name != "HEAD" else None


def is_repo(workdir: Path) -> bool:
    return git(workdir, "rev-parse", "--git-dir")[0]


def dirty(workdir: Path) -> list:
    """Paths with uncommitted changes. Empty means clean."""
    ok, out = git(workdir, "status", "--porcelain")
    return [line[3:] for line in out.splitlines() if line.strip()] if ok else []


def switch_to(workdir: Path, branch: str) -> tuple:
    """Check out `branch`, creating it from HEAD if it does not exist.

    Returns (ok, message). Existing rather than fresh is the normal case on a
    second delegation for the same issue, and reusing it is correct: the two
    attempts belong to one issue and a reviewer wants them in one place.
    """
    if git(workdir, "rev-parse", "--verify", "--quiet", branch)[0]:
        ok, out = git(workdir, "checkout", branch)
        return ok, (f"on existing branch `{branch}`" if ok else out)
    ok, out = git(workdir, "checkout", "-b", branch)
    return ok, (f"on new branch `{branch}`" if ok else out)


@contextmanager
def restored(workdir: Path):
    """Put the tree back on the branch it started on, whatever happens inside.

    The pass is what leaves state behind, so the pass is what cleans it up. A
    delegated fix stays on its own branch — nothing is discarded — but the
    checkout a human, an eval batch or the next pass finds is the one that was
    there before.

    Uncommitted work is *not* stashed or discarded. A checkout that would lose
    it fails, and the failure is logged and swallowed: leaving the tree on a fix
    branch is a mess someone can see and sort out, and throwing away an edit to
    tidy up is not.
    """
    workdir = Path(workdir)
    started = current_branch(workdir) if is_repo(workdir) else None
    try:
        yield started
    finally:
        if started and current_branch(workdir) != started:
            ok, out = git(workdir, "checkout", started)
            if ok:
                logger.info(f"Put the working tree back on `{started}`")
            else:
                logger.warning(
                    f"Could not return to `{started}`: {out}. The tree is left "
                    f"on `{current_branch(workdir)}` — check it before the "
                    f"next pass.")


# pytest's exit codes. Only the first three mean a test actually failed; the
# rest mean the suite could not be run, and reading those as failure would make
# every fix in a project without tests look like a regression. Getting this
# wrong is not hypothetical -- exit 4 (the path does not exist) was being
# reported as "the change broke this project's own test suite".
_TESTS_FAILED = {1, 2, 3}


def run_tests(workdir: Path, timeout: int) -> tuple:
    """(passed, tail) for this project's own suite.

    Run after any delegation that moved the repository, and deliberately not
    offered as a tool: a check the agent can forget to make is one it will
    forget on the run that needed it. A change that breaks the suite is not a
    fix, whatever the issue's signature does afterwards.

    **Unknown is not failure.** No pytest, no `tests/`, nothing collected -- all
    return True with a note saying nothing was checked. A guard that cannot tell
    "this broke" from "I could not look" would block every fix it could not
    measure, which is the opposite of the job.
    """
    import sys
    try:
        done = subprocess.run(
            (sys.executable, "-m", "pytest", "tests", "-q"),
            cwd=str(workdir), capture_output=True, encoding="utf-8",
            errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"The suite did not finish within {timeout}s."
    except OSError as exc:
        logger.warning(f"Could not run the suite: {exc!r}")
        return True, f"The suite could not be run ({exc!r}); nothing was checked."

    tail = "\n".join((done.stdout or done.stderr or "").splitlines()[-15:])
    if done.returncode in _TESTS_FAILED:
        return False, tail
    if done.returncode != 0:
        logger.info(f"No suite to run in {workdir} (pytest exit "
                    f"{done.returncode}); nothing was checked.")
        return True, f"No suite was run (pytest exit {done.returncode}).\n{tail}"
    return True, tail


def commit_ledger(workdir: Path, issue_id: str) -> tuple:
    """Put the ledger on the branch the pass started on, before it leaves it.

    The ledger is the loop's memory: an issue is supposed to outlive the
    session that found it. It lives inside the repository, so a pass that
    wrote it and then switched to a fix branch carried it along -- and the
    delegated session, which commits whatever it finds dirty, committed the
    diagnosis onto the fix branch. `restored` then put the checkout back and
    the starting branch had never heard of any of it. The next pass began
    from an empty ledger and re-diagnosed what was already written down,
    which is the one thing the ledger exists to prevent.

    Committing here also draws the line a reviewer wants: the diagnosis on
    the branch they already have, the fix on a branch they can take or
    leave, and neither hidden inside the other.

    Returns (ok, message). Nothing to commit is success, not an error.
    """
    ok, out = git(workdir, "status", "--porcelain", "evals/results/issues/")
    if not ok:
        return False, f"Could not check ledger status: {out}"
    if not out.strip():
        return True, "No uncommitted ledger changes"

    ok, out = git(workdir, "add", "evals/results/issues/")
    if not ok:
        return False, f"Could not stage ledger changes: {out}"

    ok, out = git(workdir, "commit", "-m", f"Update ledger for issue {issue_id}")
    if not ok:
        return False, f"Could not commit ledger changes: {out}"
    return True, "Ledger changes committed"
