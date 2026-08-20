"""Narrow tools over RestrictedShellBackend, one small schema each.

Tool schemas are 91% of what the deep-agents loop spends per step
(docs/06-agent.md#64), so every description here is deliberately terse and
every signature deliberately flat: no prose describing capabilities the backend
doesn't have, and no optional knob without a reason.

The searching tools carry exactly one, `offset`, and it buys the end of silent
truncation. They used to answer with the first 40 matches and no indication
that there had been a 41st, which turns a retrieval failure into what looks
like a reasoning failure: the model edits confidently against the half of the
answer it was shown. A model that never passes `offset` is no worse off than
before -- it just gets told what it missed.

The `execute` description in deepagents advertises a general shell
(`npm install && npm test`, `cd`, `&&`) that this backend rejects, and we have
models failing on exactly that. `run_tests` below says only what is true.
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool

MAX_TOOL_OUTPUT = 8_000
_READ_LIMIT = 200
# Results per page for the searching tools. The number is unchanged; what is
# new is that the caller is told when it was not the whole answer.
_PAGE = 40


def _clip(text: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "\n...(truncated)"


def make_tools(backend, shell: bool = False) -> dict:
    """Build the tool set bound to one backend. Returns name -> tool.

    `shell` only changes what `run_command`'s description promises. The backend
    is what actually enforces the limit, and a description that advertises
    capabilities the backend rejects is a measured cause of failed tool calls
    (docs/06-agent.md#65) -- so the two are kept in step here rather than left
    to agree by luck.
    """

    def _page(rows: list, offset: int, tool: str, **kwargs) -> str:
        """One page of results, saying plainly what is not on it.

        Silent truncation is the failure this exists to prevent: a search cut
        off before the relevant hit produces a confidently wrong edit, and the
        taxonomy scores that as `reasoning` when it was retrieval all along. So
        the true count is always stated, and the exact next call is spelled out
        rather than left for the model to infer.
        """
        shown = rows[offset:offset + _PAGE]
        if not shown:
            return (f"no more matches (offset {offset}, {len(rows)} total)"
                    if rows else "no matches")
        text = "\n".join(shown)
        remaining = len(rows) - (offset + len(shown))
        if remaining > 0:
            args = ", ".join(f"{k}={v!r}" for k, v in kwargs.items())
            text += (f"\n\n[{len(shown)} of {len(rows)} shown; {remaining} more. "
                     f"To see them: {tool}({args}, offset={offset + len(shown)})]")
        return _clip(text)

    def find_files(pattern: str, offset: int = 0) -> str:
        """Find files by glob, e.g. '**/*.py'. Returns paths, 40 per page."""
        res = backend.glob(pattern, path="/")
        if res.error:
            return f"error: {res.error}"
        paths = [m["path"] for m in res.matches if not m.get("is_dir")]
        return _page(paths, offset, "find_files", pattern=pattern)

    def search_code(pattern: str, offset: int = 0) -> str:
        """Search file contents by regex. Returns 'path:line: text', 40 per page."""
        res = backend.grep(pattern, path="/")
        if res.error:
            return f"error: {res.error}"
        hits = [f"{m['path']}:{m['line']}: {m['text'].strip()}" for m in res.matches]
        return _page(hits, offset, "search_code", pattern=pattern)

    def read_lines(file_path: str, offset: int = 0) -> str:
        """Read up to 200 lines of a file, starting at line `offset`.

        Says when the file continues past what it returned, so a model reading a
        long file knows there is more rather than assuming it saw the end."""
        res = backend.read(file_path, offset=offset, limit=_READ_LIMIT)
        if res.error:
            return f"error: {res.error}"
        content = (res.file_data or {}).get("content", "")
        lines = content.split("\n")
        numbered = "\n".join(f"{i + offset + 1}\t{line}"
                             for i, line in enumerate(lines))
        # A short read is the end of the file; a full one probably is not.
        if len(lines) >= _READ_LIMIT:
            numbered += (f"\n\n[{len(lines)} lines shown from line {offset + 1}. "
                         f"The file continues: read_lines({file_path!r}, "
                         f"offset={offset + len(lines)})]")
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

    def run_command(command: str) -> str:
        """Run a command and return its exit code and output."""
        res = backend.execute(command)
        return _clip(f"exit={res.exit_code}\n{res.output}")

    run_command.__doc__ = (
        "Run a shell command and return its exit code and output. Pipes, "
        "chaining and redirection all work."
        if shell else
        "Run a command and return its exit code and output. Only `python` and "
        "`pytest` work, named bare; there is no shell, so no pipes, no &&, no "
        "cd. To check something the tests do not cover, write a script with "
        "create_file and run it with `python <file>`.")

    fns = [find_files, search_code, read_lines, replace_in_file,
           create_file, run_tests, list_dir, run_command]
    return {f.__name__: StructuredTool.from_function(f, name=f.__name__,
                                                     description=f.__doc__)
            for f in fns}
