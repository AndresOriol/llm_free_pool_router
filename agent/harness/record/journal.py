"""The journal: append-only, one line per step, flushed before the next starts.

What survives a crash is everything that had actually happened. `replay` is the
other half of that bargain -- it rebuilds what the session *knew*, so a killed
process resumes with its knowledge rather than from zero.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agent.harness.log import Log


@dataclass
class Step:
    n: int
    action: str
    goal: str
    status: str
    finding: str
    evidence: list = field(default_factory=list)
    # The rest of the brief this step was given. `goal` alone says what was
    # asked; these two say what the node was told in order to do it, which is
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


def replay(journal: Journal, log: Log) -> int:
    """Rebuild what the session knew from the journal. Returns steps replayed.

    Only knowledge is restored, never actions: the files on disk already carry
    the edits, so re-applying them would be wrong. This is why the journal
    stores findings rather than instructions.
    """
    steps = journal.read()
    for record in steps:
        finding = record.get("finding") or ""
        if finding:
            log.note(record.get("action", "?"), finding)
        for command, code in record.get("evidence") or []:
            log.step("resumed", f"{command} -> exit {code}")
    return len(steps)
