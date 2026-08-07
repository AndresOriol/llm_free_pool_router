"""A session: one long unattended run over one project.

The pipeline in loop.py drives a *task* to a verdict. This drives a *session* --
the unit of work described in docs/design/long-run-harness.md: the agent picks
up the project's notes, works unattended on its own branch, updates the docs to
match what it changed, and writes a rationale a human can review instead of the
code.

Four things make it a session rather than a long task:

- **It commits.** Each completed unit is a commit on `session/<id>`; the human's
  gate is the merge, so nothing here can merge, push or reset.
- **It survives dying.** Every step is appended to a journal before the next one
  starts, so a killed process resumes with what it knew rather than from zero.
- **It reports.** The rationale is built from commands that actually ran and the
  exit codes they returned, not from a model's recollection of its own work.
- **It answers to the notes.** The project's notes file is the input and the
  output: the human writes feedback there, the session appends its account.

The orchestrator is the only role that sees everything, and it writes a brief
for each worker (agent/harness/envelope.py). Workers see their declared
blackboard sections plus that brief -- so context is pushed down by the one role
routed to a wide-context member, never pulled up by roles that cannot afford it.
"""

from __future__ import annotations

import json
import logging
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agent.harness.blackboard import Blackboard
from agent.harness.envelope import Brief, Report, parse_brief, parse_report
from agent.harness.loop import Stats, run_role
from agent.harness.roles import SESSION_ORCHESTRATE, SESSION_ROLES

logger = logging.getLogger("harness")

STATE_DIR = ".harness"
NOTES_FILENAMES = ("NOTES.md", "notes.md")
MAX_STEPS = 24
# Roles whose value is what they *say*; the rest are judged by what they did.
_REPORTING = {"explore", "review"}
# Consecutive explores tolerated once the files are known. Observed on the
# first live run: nine of twelve steps were `explore`, re-reading the same four
# files, because reading is the move an orchestrator can always justify. At some
# point somebody has to write something.
MAX_CONSECUTIVE_EXPLORE = 2
_EXIT_RE = re.compile(r"^exit=(-?\d+)", re.MULTILINE)


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
            self._run("add", "-A")
            self._commit("Baseline before the session")

        if not self._run("rev-parse", "HEAD").stdout.strip():
            # An empty repo has no HEAD to branch from or diff against.
            self._run("add", "-A")
            self._commit("Baseline before the session")

        self.base = self._run("rev-parse", "HEAD").stdout.strip()
        self.branch = f"session/{session_id}"
        self.enabled = bool(self.base)
        if self.enabled:
            self._run("checkout", "-b", self.branch)
        return self.enabled

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

    def as_dict(self) -> dict:
        return {"n": self.n, "action": self.action, "goal": self.goal,
                "status": self.status, "finding": self.finding,
                "evidence": self.evidence,
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
# Turning a role's raw result into a report.
# ---------------------------------------------------------------------------

def _exit_code(output: str):
    match = _EXIT_RE.search(output or "")
    return int(match.group(1)) if match else None


def build_report(action: str, result) -> Report:
    """What a role achieved, judged by the right thing for that role.

    A reporting role is taken at its word about what it *found*. An acting role
    is not: its status comes from its tool effects. A model that says "tests
    pass" about a failing run must not be able to end a session, and under a
    review model where nobody reads the code, it would end it convincingly.
    """
    if action in _REPORTING:
        return parse_report(result.text)

    evidence, ran_ok = [], None
    for call in result.calls:
        if call["name"] != "run_command":
            continue
        code = _exit_code(call["output"])
        evidence.append([str(call["args"].get("command", "?")), code])
        if code is not None:
            ran_ok = code == 0 if ran_ok is None else (ran_ok and code == 0)

    applied = [out for name, out in result.outputs
               if name in {"replace_in_file", "create_file"} and out.startswith("ok:")]

    if action == "execute":
        status = "DONE" if ran_ok else ("PARTIAL" if evidence else "BLOCKED")
        finding = (f"ran {len(evidence)} command(s); "
                   f"{'all succeeded' if ran_ok else 'something failed'}"
                   ) if evidence else "ran nothing"
    else:
        status = "DONE" if applied else "PARTIAL"
        finding = applied[-1] if applied else (result.text or "nothing applied")[:200]

    # A role that reports INSUFFICIENT_CONTEXT while doing nothing is telling
    # the orchestrator something it needs to hear, so let the text override an
    # inferred PARTIAL -- but never let it override a demonstrated success.
    if status != "DONE" and "INSUFFICIENT_CONTEXT" in (result.text or "").upper():
        return Report(status="INSUFFICIENT_CONTEXT",
                      finding=parse_report(result.text).finding, evidence=evidence)

    return Report(status=status, finding=finding, evidence=evidence)


def _absorb(action: str, report: Report, result, bb: Blackboard) -> None:
    """Fold a step into the blackboard. The only channel between roles."""
    if report.finding:
        bb.add_note(f"[{action}] {report.finding}")
    if action == "explore":
        bb.add_files(_paths(result))
    elif action in {"write", "document"}:
        for name, out in result.outputs:
            if name in {"replace_in_file", "create_file"} and out.startswith("ok:"):
                bb.add_edit(out)
    elif action == "execute":
        outputs = [out for name, out in result.outputs if name == "run_command"]
        if outputs:
            bb.set_exec(outputs[-1], _exit_code(outputs[-1]) == 0)
    bb.record(action, f"{report.status}: {report.finding[:80]}")


_PATH_RE = re.compile(r"(/[\w./\-]+\.\w+)")


def _paths(result) -> list:
    found = []
    for _, out in result.outputs:
        found.extend(_PATH_RE.findall(out))
    return found


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


# ---------------------------------------------------------------------------
# The loop.
# ---------------------------------------------------------------------------

def run_session(model, backend, toolset, task: str, workdir: Path, config=None,
                max_steps: int = MAX_STEPS, session_id: str = "") -> tuple:
    """Run one session. Returns (blackboard, stats, outcome, steps)."""
    config = config or {}
    workdir = Path(workdir)
    session_id = session_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    notes = read_notes(workdir)
    full_task = task if not notes else f"{task}\n\n# Project notes\n{notes}"
    bb = Blackboard(task=full_task)

    journal = Journal(workdir / STATE_DIR / "journal.jsonl")
    resumed = replay(journal, bb)
    if resumed:
        logger.info(f"Resuming: {resumed} step(s) replayed from the journal.")

    git = Git(workdir)
    git.start(session_id)
    bb.set_diff(git.diff())

    stats = Stats()
    step_no = resumed
    reviewed = False
    forced = None
    recent = []
    outcome = "exhausted"

    while step_no < max_steps:
        if forced:
            brief, forced = forced, None
        else:
            decision = run_role(model, SESSION_ORCHESTRATE, toolset, bb, config, stats)
            brief = parse_brief(decision.text)

        if brief.action == "GIVEUP":
            outcome = "giveup"
            break

        if brief.action == "DONE":
            # An unverified "done" is the `stopping` failure class wearing a
            # confident face. Two deterministic push-backs, then it is accepted:
            # run the code, and have someone look at it.
            if not bb.exec_ok:
                bb.record("orchestrate", "DONE refused: nothing has run yet")
                brief = Brief(action="EXECUTE", goal="Verify the change actually works.",
                              context=brief.context,
                              done_when="A command has run and its exit code is known.")
            elif not reviewed:
                bb.record("orchestrate", "DONE refused: nothing has been reviewed")
                brief = Brief(action="REVIEW", goal="Check the change does what was asked.",
                              context=brief.context,
                              done_when="You have judged the change correct or named what is wrong.")
            else:
                outcome = "done"
                break

        if (brief.action == "EXPLORE" and bb.files
                and recent[-MAX_CONSECUTIVE_EXPLORE:].count("explore")
                >= MAX_CONSECUTIVE_EXPLORE):
            bb.record("orchestrate", "EXPLORE refused: already explored twice")
            brief = Brief(
                action="WRITE",
                goal="Apply the change the task asks for.",
                context=(brief.context or "") + "\n" + "\n".join(bb.notes[-2:]),
                done_when="An edit has been applied to a file.")

        role = SESSION_ROLES.get(brief.action.lower())
        if role is None:
            bb.record("orchestrate", f"unknown action {brief.action!r}; exploring")
            role = SESSION_ROLES["explore"]
            brief = Brief(action="EXPLORE", goal=brief.goal, context=brief.context)

        step_no += 1
        result = run_role(model, role, toolset, bb, config, stats,
                          force_summary=role.reports, brief=brief)
        report = build_report(role.name, result)
        recent.append(role.name)
        _absorb(role.name, report, result, bb)
        journal.append(Step(n=step_no, action=role.name, goal=brief.goal,
                            status=report.status, finding=report.finding,
                            evidence=report.evidence))

        if role.name == "review":
            reviewed = report.status == "DONE"
        if role.name in {"write", "document"} and report.status == "DONE":
            git.commit(f"{role.name}: {(brief.goal or report.finding)[:60]}")
            bb.set_diff(git.diff())
            # "Check what you just changed" is always the right next move, so it
            # is not worth a model call to be told so.
            if role.name == "write":
                forced = Brief(action="EXECUTE",
                               goal="Check the change that was just applied.",
                               context=report.finding,
                               done_when="A command has run and its exit code is known.")

    steps = journal.read()
    reports_dir = workdir / STATE_DIR / "reports"
    rationale_path = reports_dir / f"session-{session_id}.md"
    bb.set_diff(git.diff())
    write_rationale(rationale_path, session_id, full_task, steps, outcome,
                    bb.diff, git.branch)
    append_to_notes(workdir, session_id, outcome, rationale_path,
                    _summarize(steps, outcome))
    git.commit(f"session {session_id}: {outcome}")
    return bb, stats, outcome, steps


def _summarize(steps: list, outcome: str) -> str:
    """The two-line version, for the notes file."""
    did = [s for s in steps if s.get("action") in {"write", "document"}
           and s.get("status") == "DONE"]
    ran = [c for s in steps for c in (s.get("evidence") or [])]
    return (f"{len(steps)} step(s), {len(did)} change(s) applied, "
            f"{len(ran)} command(s) run. Outcome: {outcome}.")
