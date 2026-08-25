"""Notes in, notes out: the human interface, and it is one file.

The session reads the project's notes as part of its brief and appends its own
account to the same file. That symmetry is the point -- the human writes
feedback where the last session left its report, so there is one place to look
and one place to answer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

NOTES_FILENAMES = ("NOTES.md", "notes.md")


def find_notes(workdir: Path):
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


def append_to_notes(workdir: Path, session_id: str, outcome: str,
                    rationale_path: Path, summary: str) -> None:
    """Append this session's account to the same file the human writes into."""
    path = find_notes(workdir) or (Path(workdir) / NOTES_FILENAMES[0])
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entry = (f"\n\n## Session {session_id} ({stamp}) — {outcome}\n\n{summary}\n\n"
             f"Full rationale: `{rationale_path.as_posix()}`\n")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(entry)
