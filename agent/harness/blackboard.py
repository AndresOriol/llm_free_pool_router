"""The shared state every role reads from and writes to.

This is the whole context-partitioning mechanism. A role never sees the
conversation of another role -- it sees a *rendered slice* of this object,
capped section by section. So the prompt a role receives is bounded by the
caps below rather than by how long the run has been going, which is what lets
a 6,000-TPM model serve step 40 as cheaply as step 1.

Every cap here is a token-budget decision, not a formatting preference.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Per-section caps, in characters (~chars/4 tokens). Sized so the largest
# realistic render stays near ~1,200 tokens, leaving room for tool schemas
# inside the smallest pool member's ceiling (6,000 TPM -> ~5,400 usable).
MAX_FILES = 12
MAX_NOTES = 6
MAX_NOTE_CHARS = 500
MAX_EDITS = 8
# Test output is tail-clipped, not head-clipped: pytest puts the failure
# summary at the end, which is the part a model needs to react to.
MAX_TEST_CHARS = 1_500
MAX_LOG = 10


def _clip(text: str, limit: int, *, tail: bool = False) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return ("...(clipped)\n" + text[-limit:]) if tail else (text[:limit] + "\n...(clipped)")


@dataclass
class Blackboard:
    """Everything the run knows, in a form any role can be handed a slice of."""

    task: str
    files: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    edits: list[str] = field(default_factory=list)
    last_test: str = ""
    test_passed: bool | None = None
    log: list[str] = field(default_factory=list)
    cycles: int = 0

    def add_files(self, paths) -> None:
        """Record candidate paths, newest first, deduped and capped."""
        for p in paths:
            if p and p not in self.files:
                self.files.insert(0, p)
        del self.files[MAX_FILES:]

    def add_note(self, note: str) -> None:
        note = _clip(note, MAX_NOTE_CHARS)
        if note:
            self.notes.append(note)
            del self.notes[:-MAX_NOTES]

    def add_edit(self, summary: str) -> None:
        self.edits.append(summary)
        del self.edits[:-MAX_EDITS]

    def record(self, role: str, outcome: str) -> None:
        self.log.append(f"{role}: {_clip(outcome, 120)}")
        del self.log[:-MAX_LOG]

    def set_test(self, output: str, passed: bool) -> None:
        self.last_test = _clip(output, MAX_TEST_CHARS, tail=True)
        self.test_passed = passed

    # -- rendering -------------------------------------------------------

    def render(self, sections) -> str:
        """Render only the named sections. A role declares what it needs and
        gets nothing else -- that declaration is the context budget."""
        out = []
        for name in sections:
            body = _SECTIONS[name](self)
            if body:
                out.append(body)
        return "\n\n".join(out)


def _s_task(bb: Blackboard) -> str:
    return f"# Task\n{bb.task.strip()}"


def _s_files(bb: Blackboard) -> str:
    if not bb.files:
        return ""
    return "# Candidate files\n" + "\n".join(f"- {p}" for p in bb.files)


def _s_notes(bb: Blackboard) -> str:
    if not bb.notes:
        return ""
    return "# What we know\n" + "\n\n".join(bb.notes)


def _s_edits(bb: Blackboard) -> str:
    if not bb.edits:
        return ""
    return "# Edits already applied\n" + "\n".join(f"- {e}" for e in bb.edits)


def _s_test(bb: Blackboard) -> str:
    if not bb.last_test:
        return ""
    verdict = {True: "PASSED", False: "FAILED", None: "not run"}[bb.test_passed]
    return f"# Last test run ({verdict})\n```\n{bb.last_test}\n```"


def _s_log(bb: Blackboard) -> str:
    if not bb.log:
        return ""
    return "# Steps so far\n" + "\n".join(f"- {line}" for line in bb.log)


_SECTIONS = {
    "task": _s_task,
    "files": _s_files,
    "notes": _s_notes,
    "edits": _s_edits,
    "test": _s_test,
    "log": _s_log,
}
