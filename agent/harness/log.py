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
  They are sized against the *mid* of the pool rather than its floor. The
  earlier sizing (~1,200 tokens, to fit a 6,000-TPM Groq member with room for
  tool schemas) shaped every view in the harness around three of ten pool
  members; the other seven hold 128k-250k
  ([llm_router/config.yaml](../../llm_router/config.yaml)). The router already
  excludes any member a request would overflow
  ([4.2](../../docs/04-failover.md#42-size-aware-selection)), so a richer view
  routes itself to a wider member instead of being truncated to fit the
  narrowest one -- and a step that genuinely stays small still lands on Groq.
  Size-aware selection is what decides the tier; these caps only decide what a
  node is allowed to know.
- **Clipping happens on the way in**, once, in the writer that knows what it is
  writing -- command output is clipped from the end because pytest puts the
  failure summary there, a diff from the start. A view never has to re-decide.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from langchain_core.messages import BaseMessage, HumanMessage

MAX_FILES = 24
MAX_NOTES = 12
MAX_NOTE_CHARS = 1_200
MAX_EDITS = 16
# Command output is tail-clipped, not head-clipped: pytest puts the failure
# summary at the end, which is the part a model needs to react to. 1,500 chars
# was under one real traceback: the assertion line and the short test summary
# are what a writer has to react to, and clipping to fit Groq routinely cut
# them off mid-frame.
MAX_EXEC_CHARS = 6_000
MAX_STEPS_SHOWN = 20
MAX_STEP_CHARS = 200
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
