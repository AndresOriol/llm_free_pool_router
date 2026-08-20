"""What a session writes down: git, the journal, the transcript, the rationale.

The graph (agent/harness/graph.py) decides what happens. This module is
everything that survives it -- and under a review model where the human reads
prose rather than code, that is the deliverable.

Four things make a session rather than a long task, and all four live here:

- **It commits.** Each completed unit is a commit on `session/<id>`; the human's
  gate is the merge, so nothing here can merge, push or reset.
- **It survives dying.** Every step is appended to a journal before the next one
  starts, so a killed process resumes with what it knew rather than from zero.
- **It reports.** The rationale is built from commands that actually ran and the
  exit codes they returned, not from a model's recollection of its own work.
- **It answers to the notes.** The project's notes file is the input and the
  output: the human writes feedback there, the session appends its account.

"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agent.harness.blackboard import Blackboard


logger = logging.getLogger("harness")

STATE_DIR = ".harness"
NOTES_FILENAMES = ("NOTES.md", "notes.md")


# ---------------------------------------------------------------------------
# Git. Driven by this module, never by a model: committing after a unit of work
# is not a judgement call, and a model that forgets to commit loses the session.
# ---------------------------------------------------------------------------

class Git:
    """The session's own git, scoped to the workdir.

    Separate from the model-facing allowlist in restricted_backend.py. That one
    exists to constrain what a *model* may run; this one is deterministic
    plumbing, so it is written directly rather than routed through a tool a
    small model would have to remember to call.
    """

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


# ---------------------------------------------------------------------------
# The journal. Append-only, one line per step, flushed before the next step
# starts -- so what survives a crash is everything that had actually happened.
# ---------------------------------------------------------------------------

@dataclass
class Step:
    n: int
    action: str
    goal: str
    status: str
    finding: str
    evidence: list = field(default_factory=list)
    # The rest of the brief this step was given. `goal` alone says what was
    # asked; these two say what the role was told in order to do it, which is
    # the variable the whole push-context-down design turns on. A step that
    # answered INSUFFICIENT_CONTEXT is unreadable without them.
    context: str = ""
    done_when: str = ""

    def as_dict(self) -> dict:
        return {"n": self.n, "action": self.action, "goal": self.goal,
                "status": self.status, "finding": self.finding,
                "evidence": self.evidence,
                "context": self.context, "done_when": self.done_when,
                "ts": datetime.now(timezone.utc).isoformat()}


class Journal:
    """Steps on disk, in the order they happened."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, step: Step) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(step.as_dict()) + "\n")
            handle.flush()

    def read(self) -> list:
        """Every recorded step, tolerating a half-written final line."""
        if not self.path.is_file():
            return []
        steps = []
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                steps.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # the crash landed mid-write; that step never finished
        return steps


# ---------------------------------------------------------------------------
# The transcript. What each model turn was handed, and what it said back.
# ---------------------------------------------------------------------------

TRANSCRIPT_DIR = "steps"


class Transcript:
    """Every model turn written out verbatim, one file each.

    The journal records what a step *concluded*. This records what it was given
    and what it actually replied -- and only the second pair separates a role
    that reasoned badly from a role that was briefed badly. A review that
    cannot tell those apart can name the failure but not its cause, which is
    the difference between a report and a recommendation.

    Deliberately not folded into the journal: a rendered prompt runs to a few
    kilobytes, and the journal is the crash-resume substrate, re-read line by
    line on every resume. Keeping the bulk out of it leaves that path cheap.
    """

    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        # A resumed session continues the numbering instead of overwriting the
        # turns that came before the crash.
        self.turn = len(list(self.dir.glob("*.md")))

    def write(self, role: str, result, brief=None) -> Path:
        self.turn += 1
        lines = [f"# Turn {self.turn:02d} - {role}", "",
                 f"_{datetime.now(timezone.utc).isoformat()}_"]
        if brief is not None:
            lines += ["", "## Brief it was given", "",
                      f"- **ACTION:** {brief.action}",
                      f"- **GOAL:** {brief.goal or '(none)'}",
                      f"- **CONTEXT:** {brief.context or '(none)'}",
                      f"- **DONE_WHEN:** {brief.done_when or '(none)'}"]
        lines += ["", "## Prompt", "", "```",
                  result.prompt or "(not captured)", "```",
                  "", "## Raw reply", "", "```", result.text or "(empty)", "```"]
        if result.calls:
            lines += ["", "## Tool calls", ""]
            for index, call in enumerate(result.calls, 1):
                args = json.dumps(call.get("args") or {}, default=str)
                lines += [f"{index}. `{call.get('name')}` {args[:500]}",
                          "",
                          "   ```",
                          _indent(str(call.get("output") or "")[:800]),
                          "   ```",
                          ""]
        path = self.dir / f"{self.turn:02d}-{role}.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


def _indent(text: str) -> str:
    return "\n".join("   " + line for line in text.splitlines())


def replay(journal: Journal, bb: Blackboard) -> int:
    """Rebuild what the session knew from the journal. Returns steps replayed.

    Only knowledge is restored, never actions: the files on disk already carry
    the edits, so re-applying them would be wrong. This is why the journal
    stores findings rather than instructions.
    """
    steps = journal.read()
    for record in steps:
        finding = record.get("finding") or ""
        if finding:
            bb.add_note(f"[{record.get('action', '?')}] {finding}")
        for command, code in record.get("evidence") or []:
            bb.record("resumed", f"{command} -> exit {code}")
    if steps:
        bb.cycles = len(steps)
    return len(steps)


# ---------------------------------------------------------------------------
# Notes: the human interface. The session reads the project's notes as its
# brief, and appends its account to the same file.
# ---------------------------------------------------------------------------

def find_notes(workdir: Path) -> Path | None:
    for name in NOTES_FILENAMES:
        candidate = Path(workdir) / name
        if candidate.is_file():
            return candidate
    return None


def read_notes(workdir: Path, limit: int = 4_000) -> str:
    path = find_notes(workdir)
    if path is None:
        return ""
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    return text[-limit:] if len(text) > limit else text


# ---------------------------------------------------------------------------
# The rationale: what the human actually reads.
# ---------------------------------------------------------------------------

def _changed_files(diff: str) -> set:
    """Paths a unified diff touches.

    Kept out of the f-string it used to live in: the doubled escaping there
    (`\\\\+` inside a raw string) made the pattern match a literal backslash, so
    every rationale ever written reported "Files changed: 0" while the diff
    plainly listed files. A number nobody can check is worse than no number.
    """
    return set(re.findall(r"^\+\+\+ b/(.+)$", diff or "", re.MULTILINE))


def write_rationale(path: Path, session_id: str, task: str, steps: list,
                    outcome: str, diff: str, branch: str) -> str:
    """Build the session's account of itself, from the journal.

    Every claim here is a step that was recorded or a command that ran. The
    session does not get to narrate: if it is not in the journal, it does not
    appear.
    """
    evidence = [(cmd, code) for step in steps for cmd, code in (step.get("evidence") or [])]
    open_questions = [s for s in steps
                      if s.get("status") in {"BLOCKED", "INSUFFICIENT_CONTEXT"}]

    lines = [
        f"# Session {session_id}",
        "",
        f"- **Outcome:** {outcome}",
        f"- **Branch:** {branch or '(not a git repo)'}",
        f"- **Steps:** {len(steps)}",
        f"- **Files changed:** {len(_changed_files(diff))}",
        "",
        "## What was asked",
        "",
        task.strip()[:1_500],
        "",
        "## What was done",
        "",
    ]
    for step in steps:
        lines.append(f"{step['n']}. **{step['action']}** ({step['status']}) — "
                     f"{(step.get('finding') or '').strip()[:300]}")
    lines += ["", "## Evidence", ""]
    if evidence:
        lines += [f"- `{cmd}` → exit {code}" for cmd, code in evidence]
    else:
        lines.append("- Nothing was executed. No claim here is backed by a run.")

    lines += ["", "## Open questions", ""]
    if open_questions:
        # R3: a session that got stuck says so in writing rather than ending
        # quietly, because nobody is watching until morning.
        lines += [f"- ({s['action']}) {(s.get('finding') or '').strip()[:300]}"
                  for s in open_questions]
    else:
        lines.append("- None recorded.")

    text = "\n".join(lines) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def append_to_notes(workdir: Path, session_id: str, outcome: str,
                    rationale_path: Path, summary: str) -> None:
    """Notes in, notes out. The same file the human writes feedback into."""
    path = find_notes(workdir) or (Path(workdir) / NOTES_FILENAMES[0])
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entry = (f"\n\n## Session {session_id} ({stamp}) — {outcome}\n\n{summary}\n\n"
             f"Full rationale: `{rationale_path.as_posix()}`\n")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(entry)


def _summarize(steps: list, outcome: str) -> str:
    """The two-line version, for the notes file."""
    did = [s for s in steps if s.get("action") in {"write", "document"}
           and s.get("status") == "DONE"]
    ran = [c for s in steps for c in (s.get("evidence") or [])]
    return (f"{len(steps)} step(s), {len(did)} change(s) applied, "
            f"{len(ran)} command(s) run. Outcome: {outcome}.")
