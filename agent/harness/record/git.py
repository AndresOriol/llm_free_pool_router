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
        """Keep `.harness/` out of the session's git.

        The journal and the per-turn transcript live inside the workdir, so
        `add -A` stages them: they land in the commits a human reviews, and in
        `diff()`. That diff is what the orchestrator, the documenter and the
        reviewer are shown as *the change made so far*, head-clipped at 6,000
        characters -- and in a recorded run it was 100% `.harness/`, clipping
        out before it reached the one edited source file. The orchestrator read
        its own transcript where the code should have been.

        `.git/info/exclude` rather than a `.gitignore`: the workdir is somebody
        else's project and the harness does not get to leave files in it.
        """
        git_dir = self._run("rev-parse", "--git-dir").stdout.strip()
        if not git_dir:
            return
        path = self.workdir / git_dir / "info" / "exclude"
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        if f"/{STATE_DIR}/" not in existing:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"\n# The session's own bookkeeping, not its work.\n"
                             f"/{STATE_DIR}/\n")

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
