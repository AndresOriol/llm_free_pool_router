"""The handoff between roles: what the orchestrator hands down, and what comes
back up.

The problem this exists to solve: splitting work into pieces small enough for a
6,000-token model starves each piece of the context it needs to be right. The
resolution is that the orchestrator -- the one role routed to a wide-context
member -- *pushes* curated context down, and the narrow roles never go looking
for it. A role's prompt is then bounded by what the orchestrator chose to
include, not by how long the session has been running.

Both directions are labelled plain text, not JSON. A small model asked for JSON
produces `<function=write_todos {...}</function>` often enough that the most
expensive optional tool in the deep-agents loop produced the first failure of
its baseline run (docs/06-agent.md#65). Labelled lines degrade gracefully: a
missing field falls back to a default, and prose around the labels is ignored
rather than fatal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# What the orchestrator may choose. Mapped to a worker, or to a terminal state.
ACTIONS = ("EXPLORE", "WRITE", "EXECUTE", "DOCUMENT", "REVIEW", "DONE", "GIVEUP")

# What a worker may report back.
STATUSES = ("DONE", "PARTIAL", "BLOCKED", "INSUFFICIENT_CONTEXT")

_FIELD_CAP = 1_200          # per field, characters
_DEFAULT_ACTION = "EXPLORE"  # read-only: the safe move when nothing parses
_DEFAULT_STATUS = "PARTIAL"  # never assume success from an unparseable reply


# Every label either direction can carry. Used as the terminator for the field
# before it, so a model that puts the whole reply on one line -- observed:
# `STATUS: DONE | FINDING: ...` -- still parses. Requiring a line start here
# silently swallowed the status into the finding.
_LABELS = ("ACTION", "GOAL", "CONTEXT", "DONE_WHEN", "STATUS", "FINDING")
_NEXT_LABEL = r"(?=\b(?:" + "|".join(_LABELS) + r")\s*:|\Z)"


def _field(text: str, name: str, limit: int = _FIELD_CAP) -> str:
    """Everything after `NAME:` up to the next label, wherever it appears."""
    match = re.search(rf"\b{name}\s*:\s*(.*?){_NEXT_LABEL}", text or "",
                      re.DOTALL | re.IGNORECASE)
    if not match:
        return ""
    # Trailing separators are common when the labels share a line.
    return match.group(1).strip().strip("|-").strip()[:limit]


def _keyword(text: str, allowed, default: str) -> str:
    """First allowed keyword appearing as a whole word, else the default.

    Deliberately not "the reply must be exactly one word": a small model that
    writes a sentence instead of a word must not be able to end a session.
    """
    upper = (text or "").upper()
    best, at = default, len(upper) + 1
    for word in allowed:
        match = re.search(rf"\b{re.escape(word)}\b", upper)
        if match and match.start() < at:
            best, at = word, match.start()
    return best


@dataclass(frozen=True)
class Brief:
    """One instruction from the orchestrator to one role."""

    action: str
    goal: str = ""
    context: str = ""
    done_when: str = ""

    def render(self) -> str:
        """The brief as the receiving role sees it."""
        parts = [f"# Your job\n{self.goal or 'Make progress on the task above.'}"]
        if self.context:
            parts.append(f"# What you need to know\n{self.context}")
        if self.done_when:
            parts.append(f"# You are finished when\n{self.done_when}")
        parts.append(
            "Reply with:\n"
            "STATUS: DONE | PARTIAL | BLOCKED | INSUFFICIENT_CONTEXT\n"
            "FINDING: <what you found or did, in under 80 words>\n"
            "Use INSUFFICIENT_CONTEXT if you were not given enough to act on, "
            "and say in FINDING exactly what is missing. Guessing is worse than "
            "asking.")
        return "\n\n".join(parts)


@dataclass(frozen=True)
class Report:
    """One role's answer back to the orchestrator."""

    status: str
    finding: str = ""
    # Commands actually run, as (command, exit_code). The substrate for the
    # session's rationale: a claim that cannot point at one of these did not
    # happen.
    evidence: list = field(default_factory=list)

    @property
    def needs_context(self) -> bool:
        return self.status == "INSUFFICIENT_CONTEXT"


def parse_brief(text: str) -> Brief:
    """Read the orchestrator's reply. Never raises; always yields an action."""
    action = _keyword(_field(text, "ACTION") or text, ACTIONS, _DEFAULT_ACTION)
    return Brief(
        action=action,
        goal=_field(text, "GOAL"),
        context=_field(text, "CONTEXT"),
        done_when=_field(text, "DONE_WHEN"),
    )


def parse_report(text: str, evidence=None) -> Report:
    """Read a worker's reply. An unparseable reply is PARTIAL, never DONE."""
    finding = _field(text, "FINDING") or (text or "").strip()[:_FIELD_CAP]
    # Scope the status search to the STATUS line when there is one: a finding
    # that merely mentions "blocked" should not become a BLOCKED status.
    line = _field(text, "STATUS", limit=80)
    status = _keyword(line, STATUSES, _DEFAULT_STATUS) if line else _DEFAULT_STATUS
    return Report(status=status, finding=finding, evidence=list(evidence or []))
