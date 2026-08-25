"""The session's own git. Driven by this file, never by a model.

Committing after a unit of work is not a judgement call, and a model that
forgets to commit loses the session -- so this is deterministic plumbing rather
than a tool a small model would have to remember to call. It is separate from
the model-facing allowlist in agent/runtime/backend.py: that one exists to
constrain what a *model* may run.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("harness")

# Where the session keeps its own bookkeeping inside the workdir. One constant
# because `_exclude_state` below has to name exactly the directory the journal
# and the transcript are written into.
STATE_DIR = ".harness"

# Never the session's work, always noise in its diff. `.harness/` is the
# session's own bookkeeping; the rest is what running the tests leaves behind,
# and the harness now runs them itself before the first edit
# (agent/harness/gate.py). Staged by `add -A`, they land in the commits a human
# reviews, in the diff the reviewer and the documenter are shown, and in the
# `files_touched` the eval metrics count.
EXCLUDED = (f"/{STATE_DIR}/", ".pytest_cache/", "__pycache__/", "*.pyc")


class Git:
    """The session's git, scoped to the workdir."""

    def __init__(self, workdir: Path):
        self.workdir = Path(workdir)
        self.base = ""
        self.branch = ""
        self.enabled = False

    def _run(self, *args: str):
        return subprocess.run(["git", *args], cwd=str(self.workdir), text=True,
                              capture_output=True, errors="replace")

    def start(self, session_id: str) -> bool:
        """Ensure a repo exists, record the starting commit, branch off it.

        A scenario workdir is materialized by `git archive`, so it has no
        history at all. Initializing one is what makes "the diff of this
        session" a well-defined thing rather than a guess.
        """
        try:
            inside = self._run("rev-parse", "--is-inside-work-tree")
        except FileNotFoundError:
            logger.warning("git not on PATH; the session will not commit.")
            return False

        if inside.returncode != 0:
            self._run("init", "-q")

        # Before anything is staged, so the session's own bookkeeping never
        # enters its history or its diff.
        self._exclude_state()

        if not self._head():
            # An empty repo has no HEAD to branch from or diff against.
            self._run("add", "-A")
            self._commit("Baseline before the session")

        self.base = self._head()
        self.branch = f"session/{session_id}"
        self.enabled = bool(self.base)
        if self.enabled:
            self._run("checkout", "-b", self.branch)
        return self.enabled

    def _head(self) -> str:
        """The current commit, or "" when there isn't one.

        Not `rev-parse HEAD`: on a repo with no commits that prints the literal
        string `HEAD` to stdout and fails, so its output cannot be used as a
        truth test. `--verify` prints nothing instead, which is the difference
        between recording a base commit and recording the word "HEAD" as one.
        """
        result = self._run("rev-parse", "--verify", "HEAD")
        return result.stdout.strip() if result.returncode == 0 else ""

    def _exclude_state(self) -> None:
        """Keep the session's bookkeeping and test droppings out of its git.

        The journal and the per-turn transcript live inside the workdir, so
        `add -A` stages them: they land in the commits a human reviews, and in
        `diff()`. That diff is what the orchestrator, the documenter and the
        reviewer are shown as *the change made so far*, head-clipped at 6,000
        characters -- and in a recorded run it was 100% `.harness/`, clipping
        out before it reached the one edited source file. The orchestrator read
        its own transcript where the code should have been.

        `.pytest_cache/` and `__pycache__/` are the same failure with a
        different author: the session runs the suite at least twice now, once
        before the first edit and once at the gate, so without this every
        session would report itself as having touched files it only ever read.

        `.git/info/exclude` rather than a `.gitignore`: the workdir is somebody
        else's project and the harness does not get to leave files in it.
        """
        git_dir = self._run("rev-parse", "--git-dir").stdout.strip()
        if not git_dir:
            return
        path = self.workdir / git_dir / "info" / "exclude"
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        missing = [p for p in EXCLUDED if p not in existing]
        if missing:
            with path.open("a", encoding="utf-8") as handle:
                handle.write("\n# Not the session's work: its bookkeeping, and "
                             "what running the tests leaves behind.\n")
                handle.write("\n".join(missing) + "\n")

    def _commit(self, message: str) -> bool:
        # Identity may be unset on a fresh machine; -c keeps it out of global
        # config, and an unattended run must not stop to ask who it is.
        result = self._run("-c", "user.name=harness",
                           "-c", "user.email=harness@localhost",
                           "commit", "-q", "-m", message)
        return result.returncode == 0

    def commit(self, message: str) -> bool:
        """Stage everything and commit. False when there was nothing to commit."""
        if not self.enabled:
            return False
        self._run("add", "-A")
        return self._commit(message)

    def diff(self) -> str:
        """Everything this session has changed, committed or not."""
        if not self.enabled:
            return ""
        self._run("add", "-A")
        return self._run("diff", "--cached", self.base).stdout
