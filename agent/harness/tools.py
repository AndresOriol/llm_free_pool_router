"""Narrow tools over RestrictedShellBackend, one small schema each.

Tool schemas are 91% of what the deep-agents loop spends per step
(docs/06-agent.md#64), so every description here is deliberately terse and
every signature deliberately flat: no optional knobs a small model can get
wrong, and no prose describing capabilities the backend doesn't have.

The `execute` description in deepagents advertises a general shell
(`npm install && npm test`, `cd`, `&&`) that this backend rejects, and we have
models failing on exactly that. `run_tests` below says only what is true.
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool

MAX_TOOL_OUTPUT = 4_000
_READ_LIMIT = 200


def _clip(text: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "\n...(truncated)"


def make_tools(backend) -> dict:
    """Build the tool set bound to one backend. Returns name -> tool."""

    def find_files(pattern: str) -> str:
        """Find files by glob, e.g. '**/*.py'. Returns paths."""
        res = backend.glob(pattern, path="/")
        if res.error:
            return f"error: {res.error}"
        paths = [m["path"] for m in res.matches if not m.get("is_dir")]
        return "\n".join(paths[:40]) or "no matches"

    def search_code(pattern: str) -> str:
        """Search file contents by regex. Returns 'path:line: text' matches."""
        res = backend.grep(pattern, path="/")
        if res.error:
            return f"error: {res.error}"
        hits = [f"{m['path']}:{m['line']}: {m['text'].strip()}" for m in res.matches]
        return _clip("\n".join(hits[:40])) or "no matches"

    def read_lines(file_path: str, offset: int = 0) -> str:
        """Read up to 200 lines of a file, starting at line `offset`."""
        res = backend.read(file_path, offset=offset, limit=_READ_LIMIT)
        if res.error:
            return f"error: {res.error}"
        content = (res.file_data or {}).get("content", "")
        numbered = "\n".join(f"{i + offset + 1}\t{line}"
                             for i, line in enumerate(content.split("\n")))
        return _clip(numbered)

    def replace_in_file(file_path: str, old_text: str, new_text: str) -> str:
        """Replace an exact snippet in a file. `old_text` must match exactly, once."""
        res = backend.edit(file_path, old_text, new_text)
        if res.error:
            return f"error: {res.error}"
        return f"ok: replaced {res.occurrences} occurrence(s) in {file_path}"

    def create_file(file_path: str, content: str) -> str:
        """Write a file, replacing it entirely if it exists."""
        res = backend.write(file_path, content)
        if getattr(res, "error", None):
            return f"error: {res.error}"
        return f"ok: wrote {file_path}"

    def run_tests(command: str = "python -m pytest") -> str:
        """Run tests. Only `python` and `pytest` work; there is no shell, so no
        pipes, no &&, no cd. Paths are relative to the project root."""
        res = backend.execute(command)
        return _clip(f"exit={res.exit_code}\n{res.output}")

    def list_dir(path: str = "/") -> str:
        """List files in a directory."""
        res = backend.ls(path)
        if res.error:
            return f"error: {res.error}"
        return "\n".join(("%s%s" % (e["path"], "/" if e.get("is_dir") else ""))
                         for e in res.entries[:60]) or "empty"

    fns = [find_files, search_code, read_lines, replace_in_file,
           create_file, run_tests, list_dir]
    return {f.__name__: StructuredTool.from_function(f, name=f.__name__,
                                                     description=f.__doc__)
            for f in fns}
