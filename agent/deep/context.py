"""What the project looks like, stated once at the top of the session.

dcode injects this on every model call through a `LocalContextMiddleware`. Here
it is built once and appended to the system prompt, which is the cheaper of two
equivalent options for a session that works exactly one repository: the tree and
the branch barely move within a run, and on a pool where a step costs a request
against a daily quota, re-sending them every call buys nothing.

The reason it exists at all is the same as dcode's. Without it the first thing
any agent does is spend two or three tool calls discovering the shape of the
project -- and those calls are the most expensive ones in the run, because they
happen before summarization has anything to compact.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# Directories that are never worth a line: caches, virtualenvs, and the vendored
# trees that would otherwise dominate a listing.
_SKIP = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
         ".venv", "venv", "node_modules", ".idea", ".vscode", "dist", "build",
         ".eggs", ".tox"}

MAX_ENTRIES = 200
MAX_DEPTH = 3


def _git(workdir: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", "-C", str(workdir), *args],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def tree(workdir: Path, max_entries: int = MAX_ENTRIES,
         max_depth: int = MAX_DEPTH) -> str:
    """A depth-limited listing of the project, as jail-relative paths.

    Truncated rather than complete: past a couple of hundred entries this stops
    being orientation and starts being the context budget.
    """
    workdir = Path(workdir)
    lines: list[str] = []
    truncated = False

    def walk(directory: Path, depth: int) -> None:
        nonlocal truncated
        if depth > max_depth or truncated:
            return
        try:
            entries = sorted(directory.iterdir(),
                             key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            return
        for entry in entries:
            if entry.name in _SKIP or entry.name.startswith("."):
                continue
            if len(lines) >= max_entries:
                truncated = True
                return
            rel = entry.relative_to(workdir).as_posix()
            lines.append(f"/{rel}/" if entry.is_dir() else f"/{rel}")
            if entry.is_dir():
                walk(entry, depth + 1)

    walk(workdir, 1)
    if truncated:
        lines.append(f"... (listing stopped at {max_entries} entries)")
    return "\n".join(lines)


def section(workdir: Path) -> str:
    """The `### Project` section, or '' when there is nothing to say."""
    workdir = Path(workdir)
    parts: list[str] = []

    branch = _git(workdir, "rev-parse", "--abbrev-ref", "HEAD")
    if branch:
        parts.append(f"Git branch: `{branch}`")
        status = _git(workdir, "status", "--porcelain")
        if status:
            changed = len(status.splitlines())
            parts.append(f"Uncommitted changes: {changed} file(s)")
        else:
            parts.append("Working tree is clean.")

    listing = tree(workdir)
    if listing:
        parts.append("Files:\n```\n" + listing + "\n```")

    if not parts:
        return ""
    return "### Project\n\n" + "\n\n".join(parts)
