"""The ledger: a named failure, what was done about it, and whether it came back.

This is the only durable thing the improvement agent produces, and it is what
makes the loop a loop rather than a series of unrelated readings. A session that
starts cold reads this before it reads a trace: what is already known, what has
already been fixed, and what has already come back once.

Three properties carry the design.

**An issue is named and counted, not described.** A category with one instance
is an anecdote -- the rule the J2 method already applies to a batch
(`.claude/skills/j2-error-analysis`). So `evidence` is a list of run ids and
`title` is a claim about a pattern, never about a run.

**An issue carries a signature, and the signature is what closes it.** A fix is
believed when the runs recorded *after* it no longer match, not when the coding
agent says it is done. That is the one check a model cannot talk its way past,
and it is why the signature is stored with the issue instead of being re-derived
by whoever looks next ([records.matches](records.py)).

**Nothing is ever deleted.** Status moves through the file's `history`, which is
append-only. An issue that closed and reopened is the most informative row in
the ledger, and a store that overwrote it would have thrown away the finding.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from agent.improve import records as records_mod

# Beside `reports/`, `reviews/` and `runs/`, because it indexes the same runs
# and a reader who found one directory has found them all.
ISSUES_DIR = Path("evals") / "results" / "issues"

OPEN = "open"           # seen, diagnosed or not, nothing done yet
FIXING = "fixing"       # a task has been handed to the coding agent
CLOSED = "closed"       # runs recorded after the fix no longer match
REOPENED = "reopened"   # they matched again

# Not a status -- a verdict a check can return without touching the status.
# An issue whose signature has never matched a run has no baseline to have
# moved away from, so "nothing matched" says nothing about it either way.
UNPROVEN = "unproven"
STATES = (OPEN, FIXING, CLOSED, REOPENED)

_SLUG = re.compile(r"[^a-z0-9]+")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slug(text: str) -> str:
    """A file-safe id from a title. Short, because it is quoted in prose."""
    made = _SLUG.sub("-", (text or "").strip().lower()).strip("-")
    return (made[:48].rstrip("-")) or "issue"


@dataclass
class Issue:
    """One recurring failure, its diagnosis, and its fate."""

    id: str
    title: str
    status: str = OPEN
    severity: str = "medium"            # low | medium | high
    summary: str = ""                   # what goes wrong, in behaviour terms
    diagnosis: str = ""                 # why, named against the source
    fix: str = ""                       # what to change, and where
    lever: str = ""                     # the file or knob the fix lands in
    signature: dict = field(default_factory=dict)
    evidence: list = field(default_factory=list)     # run ids that showed it
    first_seen: str = ""
    last_seen: str = ""
    created: str = ""
    updated: str = ""
    tasks: list = field(default_factory=list)        # delegations to `code`
    checks: list = field(default_factory=list)       # verification passes
    history: list = field(default_factory=list)      # append-only

    def note(self, event: str, detail: str = "") -> None:
        self.history.append({"ts": now(), "event": event, "detail": detail})

    def row(self) -> str:
        return (f"{self.id}  [{self.status}/{self.severity}]  {self.title}  "
                f"({len(self.evidence)} run(s), "
                f"{len(self.checks)} check(s), lever: {self.lever or '?'})")

    def render(self) -> str:
        """The whole issue as text, for a model that has to act on it."""
        lines = [f"# {self.id} — {self.title}", "",
                 f"- **Status:** {self.status} · **Severity:** {self.severity}",
                 f"- **Lever:** {self.lever or 'not named'}",
                 f"- **Seen:** {len(self.evidence)} run(s), "
                 f"{self.first_seen[:19] or '?'} → {self.last_seen[:19] or '?'}",
                 f"- **Signature:** `{json.dumps(self.signature, sort_keys=True)}`",
                 ""]
        for heading, body in (("What goes wrong", self.summary),
                              ("Diagnosis", self.diagnosis),
                              ("Fix", self.fix)):
            if body:
                lines += [f"## {heading}", "", body, ""]
        if self.evidence:
            lines += ["## Evidence", ""] + [f"- `{r}`" for r in self.evidence] + [""]
        if self.tasks:
            lines += ["## Delegated", ""]
            lines += [f"- {t.get('ts', '')[:19]} `{t.get('task_id', '?')}` → "
                      f"{t.get('agent', '?')} — {t.get('state', '?')}"
                      for t in self.tasks] + [""]
        if self.checks:
            lines += ["## Checks", ""]
            lines += [f"- {c.get('ts', '')[:19]}: {c.get('matched', '?')} of "
                      f"{c.get('considered', '?')} run(s) since "
                      f"{str(c.get('since') or 'the beginning')[:19]} still match "
                      f"→ {c.get('verdict', '?')}" for c in self.checks] + [""]
        return "\n".join(lines).rstrip()


class IssueStore:
    """Every issue, one JSON file each, under `evals/results/issues/`."""

    def __init__(self, root: Path) -> None:
        self.directory = Path(root) / ISSUES_DIR

    def path(self, issue_id: str) -> Path:
        return self.directory / f"{issue_id}.json"

    def get(self, issue_id: str) -> Optional[Issue]:
        path = self.path(issue_id)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        known = {f for f in Issue.__dataclass_fields__}
        return Issue(**{k: v for k, v in raw.items() if k in known})

    def list(self) -> list:
        """Every issue, open ones first, then by most recently updated."""
        if not self.directory.is_dir():
            return []
        found = [self.get(p.stem) for p in sorted(self.directory.glob("*.json"))]
        order = {REOPENED: 0, OPEN: 1, FIXING: 2, CLOSED: 3}
        return sorted((i for i in found if i is not None),
                      key=lambda i: (order.get(i.status, 9), i.updated),
                      reverse=False)

    def save(self, issue: Issue) -> Path:
        issue.updated = now()
        if not issue.created:
            issue.created = issue.updated
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.path(issue.id)
        path.write_text(json.dumps(asdict(issue), indent=2, ensure_ascii=False),
                        encoding="utf-8")
        return path

    def summary(self) -> str:
        issues = self.list()
        if not issues:
            return ("No issues in the ledger yet. Nothing has been diagnosed, "
                    "so this pass starts from the traces.")
        return "\n".join(i.row() for i in issues)


def upsert(store: IssueStore, fields: dict) -> Issue:
    """Create or amend one issue from a flat dict of fields.

    One entry point rather than create/update, because the caller is a model and
    two tools that differ only in whether the thing already exists is a choice it
    should not have to make correctly. What it cannot do is silently lose
    something: `evidence` and `tasks` merge, and every write leaves a `history`
    line.
    """
    issue_id = str(fields.get("id") or slug(str(fields.get("title", ""))))
    existing = store.get(issue_id)
    issue = existing or Issue(id=issue_id, title=str(fields.get("title", issue_id)))

    for key in ("title", "summary", "diagnosis", "fix", "lever", "severity"):
        if fields.get(key):
            setattr(issue, key, str(fields[key]))

    if fields.get("signature"):
        issue.signature = dict(fields["signature"])

    for run_id in fields.get("evidence") or []:
        if run_id not in issue.evidence:
            issue.evidence.append(str(run_id))

    status = fields.get("status")
    if status:
        if status not in STATES:
            raise ValueError(f"status must be one of {', '.join(STATES)}")
        if status != issue.status:
            issue.note("status", f"{issue.status} -> {status}")
            issue.status = status

    issue.note("opened" if existing is None else "amended",
               str(fields.get("note") or ""))
    store.save(issue)
    return issue


def stamp_evidence(issue: Issue, found: list) -> None:
    """Set `first_seen`/`last_seen` from the records that matched."""
    stamps = sorted(r.ts for r in found if r.ts)
    if stamps:
        issue.first_seen = min(issue.first_seen or stamps[0], stamps[0])
        issue.last_seen = max(issue.last_seen or "", stamps[-1])


def check(issue: Issue, found: list, since: str = "") -> dict:
    """Run the signature over records and decide what the issue's status is now.

    `found` is every record considered, `since` the boundary the verification is
    about -- normally when the fix landed. The rule is deliberately blunt:

    - Something matched after the fix → the issue is not fixed. If it was
      `closed`, it **reopens**, which is the case the ledger exists to catch.
    - Nothing matched, and there was at least one run after the fix to not match
      → **closed**.
    - Nothing matched and nothing ran → unchanged, and the report says so. An
      issue must never close because nobody looked.
    - Nothing matched, but the signature has never matched any run (evidence is
      empty and no previous check had matched > 0) → unchanged, and the verdict
      is "unproven". An issue must never close because its signature never
      looked at anything.
    """
    considered = [r for r in found if not since or (r.ts and r.ts > since)]
    matched = [r for r in considered if records_mod.matches(r, issue.signature)]

    never_matched = (not issue.evidence
                     and not any(c.get("matched", 0) > 0 for c in issue.checks))

    if matched:
        verdict = REOPENED if issue.status == CLOSED else issue.status
    elif considered:
        if never_matched:
            verdict = UNPROVEN
        else:
            verdict = CLOSED
    else:
        verdict = issue.status

    report = {"ts": now(), "since": since, "considered": len(considered),
              "matched": len(matched), "verdict": verdict,
              "matching_runs": [r.id for r in matched[:20]]}
    issue.checks.append(report)
    if verdict != issue.status and verdict != UNPROVEN:
        issue.note("status", f"{issue.status} -> {verdict} (check)")
        issue.status = verdict
    stamp_evidence(issue, matched)
    return report
