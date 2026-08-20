"""Everything the session knows, as one ordered log of messages.

This is the whole context-partitioning mechanism. A node never sees another
node's conversation -- it is handed the entries of the kinds it declared, as
messages, and nothing else. So the prompt a node receives is bounded by that
declaration rather than by how long the run has been going, which is what lets a
6,000-TPM model serve step 40 as cheaply as step 1.

**It is a log, not a set of slots.** Entries are appended, never mutated: each
one records what happened, who wrote it, and what kind of thing it is. A view
takes the last few entries of the kinds asked for. That is why there is one
table below instead of a field, a setter and a renderer per kind -- and why
printing `log.entries` shows the session's whole state in the order it arrived.

Two rules keep this cheap:

- **Every cap here is a token-budget decision**, not a formatting preference.
  They are sized so the largest realistic view stays near ~1,200 tokens, leaving
  room for tool schemas inside the smallest pool member's ceiling (6,000 TPM ->
  ~5,400 usable).
- **Clipping happens on the way in**, once, in the writer that knows what it is
  writing -- command output is clipped from the end because pytest puts the
  failure summary there, a diff from the start. A view never has to re-decide.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from langchain_core.messages import BaseMessage, HumanMessage

MAX_FILES = 12
MAX_NOTES = 6
MAX_NOTE_CHARS = 500
MAX_EDITS = 8
# Command output is tail-clipped, not head-clipped: pytest puts the failure
# summary at the end, which is the part a model needs to react to.
MAX_EXEC_CHARS = 1_500
MAX_STEPS_SHOWN = 10
MAX_STEP_CHARS = 120
# The session's own diff, shown to the nodes that judge it (document, review).
# Both of those are routed to wide-context members, so this cap is generous
# compared with the rest -- it is the one kind whose whole value is detail.
MAX_DIFF_CHARS = 6_000


@dataclass(frozen=True)
class Kind:
    """How one kind of entry is shown, and how much of it survives."""

    heading: str
    keep: int         # entries of this kind rendered, newest last
    join: str = "\n"


# The whole vocabulary of the log. A node declares kinds from this table
# (Node.reads) and is handed exactly those.
KINDS = {
    "task": Kind("# Task", keep=1),
    "files": Kind("# Candidate files", keep=MAX_FILES),
    "notes": Kind("# What we know", keep=MAX_NOTES, join="\n\n"),
    "edits": Kind("# Edits already applied", keep=MAX_EDITS),
    "exec": Kind("# Last command run", keep=1),
    "diff": Kind("# The change made so far", keep=1),
    "steps": Kind("# Steps so far", keep=MAX_STEPS_SHOWN),
}


def _clip(text: str, limit: int, *, tail: bool = False) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return ("...(clipped)\n" + text[-limit:]) if tail else (text[:limit] + "\n...(clipped)")


@dataclass(frozen=True)
class Entry:
    """One thing the session learned, and who learned it."""

    kind: str
    author: str
    text: str
    # Only an exec entry sets this. The prose says SUCCEEDED or FAILED for the
    # model; this says it for the edges, which need a boolean rather than a
    # sentence they would have to parse back (agent/harness/graph.py).
    ok: Optional[bool] = None


class Log:
    """The session's whole state, appended to and viewed from."""

    def __init__(self, task: str = ""):
        self.entries: list = []
        self.add("task", "session", task.strip())

    # -- writing ---------------------------------------------------------
    #
    # One writer per kind, because the writer is where the cap belongs: it is
    # the only place that knows whether it is holding a path, a finding or
    # 40KB of pytest output.

    def add(self, kind: str, author: str, text: str, ok: Optional[bool] = None) -> None:
        if text:
            self.entries.append(Entry(kind=kind, author=author, text=text, ok=ok))

    def note(self, author: str, finding: str) -> None:
        self.add("notes", author, _clip(f"[{author}] {finding}", MAX_NOTE_CHARS))

    def files(self, author: str, paths) -> None:
        """Record candidate paths, deduped: the same file found twice is one fact."""
        known = set(self.texts("files"))
        for path in paths:
            line = f"- {path}"
            if path and line not in known:
                known.add(line)
                self.add("files", author, line)

    def edit(self, author: str, summary: str) -> None:
        self.add("edits", author, f"- {summary}")

    def ran(self, author: str, output: str, ok: bool) -> None:
        verdict = {True: "SUCCEEDED", False: "FAILED"}[bool(ok)]
        body = _clip(output, MAX_EXEC_CHARS, tail=True)
        self.add("exec", author, f"({verdict})\n```\n{body}\n```", ok=ok)

    def diff(self, diff: str) -> None:
        body = _clip(diff, MAX_DIFF_CHARS)
        self.add("diff", "session", f"```diff\n{body}\n```" if body else "")

    def step(self, author: str, outcome: str) -> None:
        self.add("steps", author, f"- {author}: {_clip(outcome, MAX_STEP_CHARS)}")

    # -- reading ---------------------------------------------------------

    def texts(self, kind: str) -> list:
        """Every entry of one kind, oldest first, uncapped."""
        return [e.text for e in self.entries if e.kind == kind]

    @property
    def exec_ok(self) -> Optional[bool]:
        """How the last command went, or None if nothing has run."""
        for entry in reversed(self.entries):
            if entry.kind == "exec":
                return entry.ok
        return None

    def view(self, kinds) -> list:
        """The slice one node is allowed to see, as messages.

        A node declares its kinds and gets exactly those, each capped. That
        declaration *is* the context budget -- there is no way for a node to
        reach past it, which is why the orchestrator has to push what a node
        needs into its brief (agent/harness/protocol.py).
        """
        messages: list[BaseMessage] = []
        for kind in kinds:
            spec = KINDS[kind]
            shown = self.texts(kind)[-spec.keep:]
            if shown:
                messages.append(HumanMessage(
                    content=spec.heading + "\n" + spec.join.join(shown)))
        return messages
