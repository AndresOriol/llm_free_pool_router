"""What the repository says a coding session did, and how to say it in one line.

Two callers: the CLI's own summary, which is what anyone who ran
`python -m agent.code` reads back, and the improvement agent checking a fix it
delegated ([16. Delegation](../../docs/16-delegation.md)).

**The prose a session writes about itself is not evidence.** The first
delegation this project ever made came back describing three changes to
`session.py` that the diff did not contain
([19.9](../../docs/19-improvement-agent.md#199-what-the-first-live-pass-showed)).
The verdict from `git` goes first, and a caller reading "no commit, and nothing
changed on disk" cannot accept "I made three changes" from the same message.

A workspace that is not a git repository is a normal, supported case: `state`
then answers `{"git": False}` and the files are simply on disk where the caller
mounted them.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("harness.code")

# `git` is asked for state, never for changes: every command below is a read.
# A session's own commits are made inside the jail by the agent, which is the
# only thing that should be writing history here.
_GIT_TIMEOUT = 30


def _git(workdir: Path, *args: str) -> Optional[str]:
    """One read-only git command, or None if it could not be answered.

    None rather than an exception for every failure mode -- not a repository,
    no git on PATH, an empty repository with no HEAD yet. Reporting git state
    is how the caller reviews the work; failing to report it must not fail work
    that is already done and already committed.
    """
    try:
        done = subprocess.run(("git", *args), cwd=str(workdir),
                              capture_output=True, encoding="utf-8",
                              errors="replace",
                              timeout=_GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug(f"git {' '.join(args)} failed: {exc!r}")
        return None
    if done.returncode != 0:
        return None
    # `rstrip`, never `strip`: `status --porcelain` encodes the state in the
    # first two columns, so an unstaged change makes the first line start with
    # a space. Stripping it shifted the slice below by one and reported `a.py`
    # as `.py` -- in the list a caller reads to see what was left behind, which
    # is exactly where a wrong path is least likely to be noticed.
    return done.stdout.rstrip()


def head(workdir: Path) -> Optional[str]:
    """The commit a task is about to start from, or None."""
    return _git(Path(workdir), "rev-parse", "HEAD")


def state(workdir: Path, before: Optional[str]) -> dict:
    """Branch, head, and what moved since `before`. The reviewable summary.

    `before` is the head this task started from. Without it the diff would be
    against the working tree only, which reports uncommitted noise and misses
    the commits that are the actual deliverable.
    """
    workdir = Path(workdir)
    now = head(workdir)
    if now is None:
        return {"git": False}

    found: dict = {"git": True, "head": now,
                   "branch": _git(workdir, "rev-parse", "--abbrev-ref", "HEAD")}

    if before:
        found["head_before"] = before
        count = _git(workdir, "rev-list", "--count", f"{before}..{now}")
        found["commits"] = int(count) if (count or "").isdigit() else 0
        changed = _git(workdir, "diff", "--name-only", before, now) or ""
        found["files_changed"] = [p for p in changed.splitlines() if p.strip()]

    # Anything the agent left uncommitted. Worth naming: a run that edited
    # files and never committed them looks identical to one that did nothing,
    # in every field above.
    dirty = _git(workdir, "status", "--porcelain")
    if dirty:
        found["uncommitted"] = [line[3:] for line in dirty.splitlines()
                                if line.strip()]
    return found


def moved(found: dict) -> bool:
    """Did the repository actually change?

    True when there is no git report at all: this is a guard against a session
    that demonstrably did nothing, not a requirement that every session prove
    itself, and a workspace with no history must not read as a failure.
    """
    if not isinstance(found, dict) or found.get("git") is not True:
        return True
    return bool(found.get("commits") or found.get("files_changed")
                or found.get("uncommitted"))


def render(found: dict) -> str:
    """One line of prose for a caller, verdict first, or '' if there is none.

    This existed once, was computed, stored and then dropped on the floor before
    any caller saw it. The first improvement pass delegated a fix, got back
    `{"commits": 0, "files_changed": []}`, was shown none of it, read the
    delegate's prose claim to have changed three things, and believed it. The
    evidence that would have settled it was already in the object.
    """
    if not isinstance(found, dict):
        return ""
    if found.get("git") is False:
        return "The workspace is not a git repository; files are on disk only."
    if found.get("git") is not True:
        return ""

    commits = found.get("commits")
    changed = found.get("files_changed")
    uncommitted = found.get("uncommitted") or []
    branch = found.get("branch") or "?"

    if commits == 0 and not changed and not uncommitted:
        return (f"**Nothing changed.** On `{branch}`, no commit was made and "
                f"the working tree is clean — the head is the same one the task "
                f"started from. Whatever the closing message says it did, the "
                f"repository disagrees.")

    parts = [f"On `{branch}`"]
    if commits is not None:
        parts.append(f"{commits} commit(s)")
    if changed:
        shown = ", ".join(f"`{p}`" for p in list(changed)[:8])
        parts.append(f"{len(changed)} file(s) changed: {shown}"
                     + (" …" if len(changed) > 8 else ""))
    if uncommitted:
        parts.append(f"**{len(uncommitted)} left uncommitted**")
    return " · ".join(parts) + "."
