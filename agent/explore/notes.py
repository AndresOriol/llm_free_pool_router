"""The research directory: where it is, and the tool that says what is in it.

Two things live here because they are the same fact seen from two sides.

**Where.** A run writes into one directory under the workdir, named when the
agent is launched rather than fixed at `research/`. That is what makes a second
question a *continuation*: point a run at the directory a previous one filled
and its notes are there to read, extend and cite; point it at a new one and the
two investigations stay apart. One workdir accumulating every question anyone
ever asked was the previous behaviour, and it made "is this note about my
question?" a thing the agent had to work out from filenames
([15.5.2](../../docs/15-explorer.md#1552-the-research-directory-and-how-to-see-it)).

Because the directory is chosen at launch, every prompt and tool description
that names it has to be told: `retarget` does that substitution once, at
assembly, so the prose can go on saying `/research/` and mean whatever this run
was given.

**What is in it.** `research_status` lists it -- path, size and title for every
note. This replaced a listing injected into the system prompt on every call.
The injection was cheaper per use (no model call) and wrong in the way that
matters: it was paid for on every call including the great majority that never
needed it, it grew the one prompt this agent already had too much of, and it
could not answer the question the agent actually asks, which is *what is in this
note* rather than *what notes exist*. A tool is asked when the answer is wanted,
and it is the seam where "list the paths" grows into "say what state the
research is in" without touching a prompt.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("harness.explore")

# The default, and the name the prompts are written against. Anything else is
# substituted in by `retarget`.
DEFAULT_DIR = "research"

# Enough for any run this agent is asked to do; past it the listing has stopped
# being orientation and started being the context budget.
MAX_NOTES = 200
# One line each, so a long first heading cannot bury the list.
MAX_TITLE = 90


def retarget(text: str, research_dir: str) -> str:
    """`text` with every `/research/` in it pointed at this run's directory.

    A blunt substitution over assembled prose, on purpose. The alternative --
    a `{research_dir}` placeholder in each of the thirty-odd places the prompts
    and tool descriptions name the directory -- is thirty chances to ship a
    literal brace to a model, and it makes the ported prompts stop being
    diffable against upstream ([deep_prompts.py](deep_prompts.py)).
    """
    target = research_dir.strip("/") or DEFAULT_DIR
    if target == DEFAULT_DIR:
        return text
    return text.replace(f"/{DEFAULT_DIR}/", f"/{target}/")


def _title(path: Path) -> str:
    """A note's first heading, or its first line of prose.

    Reads until the first non-empty line rather than loading the file: the point
    is to say what a note is about, and a note that does not say so in its first
    line is one the agent has to open anyway.
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


def listing(research_dir: Path, mount: str = DEFAULT_DIR) -> str:
    """One line per note, or '' when there are none.

    `mount` is how the paths are spelled back to the model -- the jail-absolute
    directory it writes to -- while `research_dir` is where they are on disk.
    """
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
        lines.append(f"/{mount}/{rel} ({note.stat().st_size:,} bytes)"
                     + (f" — {title}" if title else ""))
    if len(notes) > MAX_NOTES:
        lines.append(f"... ({len(notes) - MAX_NOTES} more not listed)")
    return "\n".join(lines)


def make_status_tool(research_dir: Path, mount: str = DEFAULT_DIR):
    """The `research_status` tool, bound to one directory."""
    from langchain_core.tools import StructuredTool

    research_dir = Path(research_dir)

    def research_status() -> str:
        """List every file the research has produced so far.

        Your only way to see what is on disk: there is no `ls`, `glob` or
        `grep`. Returns each note's path, size and first heading, so you can
        pick one and open it with `read_file`.

        Call it when you need to know the state of the work rather than out of
        habit — before writing a report, so you do not overwrite a note about
        another question; before choosing a filename, since another researcher
        may have taken the one you wanted; and after a long stretch of
        searching, when notes you wrote earlier have fallen out of your context
        but not off the disk.

        A directory with files in it that you did not write is a previous
        investigation this run was pointed at. Read those before repeating
        their searches, and cite them as what they are.
        """
        body = listing(research_dir, mount)
        if not body:
            return (f"/{mount}/ is empty — nothing has been written yet. The "
                    f"files you write there are this run's only deliverable.")
        return f"Files in /{mount}/:\n\n{body}"

    return StructuredTool.from_function(
        func=research_status, name="research_status",
        description=research_status.__doc__)
