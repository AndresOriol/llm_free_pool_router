"""What is already in `/research/`, restated on every model call.

The explorer has no `ls` ([tools.py](tools.py)), and this is what it has
instead: the contents of its own research directory -- every note's path, size
and title -- appended to the system message before each call.

**Why this one is per-call and the project tree is not.** The coding agent's
equivalent ([agent/code/context.py](../code/context.py)) is built once and baked
into the system prompt, on the argument that a repository barely moves within a
run and re-sending it every call buys nothing. That argument does not survive
here, because this listing is *the agent's own output*. It changes precisely
when the agent writes a note, which is the moment the listing starts mattering:

- the orchestrator has to know whether a `final_report.md` about someone else's
  question is already sitting there before it writes over it;
- two researchers working in parallel have to not choose the same filename;
- and after summarization, a note the agent wrote an hour ago is no longer
  anywhere in the conversation. The directory is the only thing that remembers.

It costs a directory read per call, no model call, and a line per note.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Awaitable, Callable

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import SystemMessage

logger = logging.getLogger("harness.explore")

# Enough for any run this agent is asked to do; past it the listing has stopped
# being orientation and started being the context budget, the same trade
# `context.tree` makes.
MAX_NOTES = 60
# One line each, so a long first heading cannot push the listing off a screen.
MAX_TITLE = 90

_EMPTY = ("`/research/` is empty -- nothing has been written yet. The files you "
          "write there are this run's only deliverable.")


def _title(path: Path) -> str:
    """A note's first heading, or its first line of prose.

    Reads until the first non-empty line rather than loading the file: the point
    is to say what a note is about, and a note that does not say so in its first
    line is one the agent will have to open anyway.
    """
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped:
                    return stripped.lstrip("#").strip()[:MAX_TITLE]
    except OSError as exc:  # noqa: BLE001 - a listing must not end a run
        logger.info(f"Could not read {path}: {exc!r}")
    return ""


def listing(research_dir: Path) -> str:
    """One line per note under `research_dir`, or '' when there are none."""
    research_dir = Path(research_dir)
    if not research_dir.is_dir():
        return ""

    try:
        notes = sorted(p for p in research_dir.rglob("*.md") if p.is_file())
    except OSError as exc:  # noqa: BLE001
        logger.info(f"Could not list {research_dir}: {exc!r}")
        return ""

    lines = []
    for note in notes[:MAX_NOTES]:
        rel = note.relative_to(research_dir).as_posix()
        title = _title(note)
        size = note.stat().st_size
        lines.append(f"/research/{rel} ({size:,} bytes)"
                     + (f" — {title}" if title else ""))
    if len(notes) > MAX_NOTES:
        lines.append(f"... ({len(notes) - MAX_NOTES} more not listed)")
    return "\n".join(lines)


def section(research_dir: Path) -> str:
    """The `### Your research directory` section, always non-empty.

    An empty directory is worth saying out loud rather than omitting: "nothing
    is written yet" is the state in which a crash costs the whole run, and it is
    the state the agent is in for as long as it is only searching.
    """
    body = listing(research_dir)
    return ("### Your research directory\n\n"
            + (f"This is current as of this turn. You have no `ls`; this is the "
               f"listing.\n\n```\n{body}\n```\n\n"
               "Do not write over a note about a different question -- pick an "
               "unused path -- and use `edit_file` to extend one of your own."
               if body else _EMPTY))


def _append(system_message, text: str) -> SystemMessage:
    """`system_message` with `text` appended as a further text block.

    Blocks rather than a joined string, so anything the framework put on the
    original message -- cache markers among them -- is carried through.
    """
    blocks = list(system_message.content_blocks) if system_message else []
    blocks.append({"type": "text", "text": f"\n\n{text}" if blocks else text})
    return SystemMessage(content_blocks=blocks)


class ResearchNotesMiddleware(AgentMiddleware):
    """Append the current `/research/` listing to every model call."""

    def __init__(self, research_dir: Path) -> None:
        super().__init__()
        self._dir = Path(research_dir)

    def _apply(self, request):
        return request.override(
            system_message=_append(request.system_message, section(self._dir)))

    def wrap_model_call(self, request, handler: Callable):
        return handler(self._apply(request))

    async def awrap_model_call(self, request, handler: Callable[..., Awaitable]):
        return await handler(self._apply(request))
