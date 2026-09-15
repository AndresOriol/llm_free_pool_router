"""Checks for RestrictedShellBackend.execute() -- the guardrails that let the
agent run its own tests but not an arbitrary host shell.

No framework: `python -m tests.agent.test_restricted_backend` (or run the file).
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.utils.backend import RestrictedShellBackend


def _run():
    with tempfile.TemporaryDirectory() as d:
        be = RestrictedShellBackend(root_dir=d)

        # An allowlisted program runs and its output/exit code come back.
        r = be.execute('python -c "print(2 + 2)"')
        assert r.exit_code == 0, r
        assert "4" in r.output, r

        # A non-allowlisted program is rejected before running.
        r = be.execute("rm -rf /")
        assert r.exit_code == 1 and "not allowed" in r.output, r

        # shell=False neutralizes chaining: the '&&' and everything after it is
        # passed to python as literal args, so no second command runs. Python
        # errors on the unknown args -> non-zero, but nothing was chained.
        r = be.execute('python -c "print(1)" && rm -rf /')
        assert "not allowed" not in r.output, r  # it went to python, not rejected as rm
        # 'rm' never executed as a command; proof is that we got python's behavior.

        # A path-qualified program (trying to escape the allowlist) is rejected.
        r = be.execute("./evil.sh")
        assert r.exit_code == 1 and "names a path" in r.output, r

        # Shell syntax with no shell to run it is refused, not passed through
        # as a literal argument. A recorded run spent 900 of its 1,050 seconds
        # on three `python - <<'PY'` heredocs, each hanging to the 300s timeout,
        # and the step whose whole job is "run the code after an edit" learned
        # nothing about the code.
        import time
        started = time.time()
        r = be.execute("python - <<'PY'\nimport os\nprint(1)\nPY")
        assert r.exit_code == 1 and "stdin" in r.output, r
        assert time.time() - started < 5, "it must refuse, not hang"

        # `python -` reads its script from stdin, and there is no stdin here.
        assert be.execute("python -").exit_code == 1

        # A pipe is refused for the same reason.
        r = be.execute('python -c "print(1)" | head')
        assert r.exit_code == 1 and "no shell" in r.output, r

        # ...but `<<` inside a code string is just Python, and must still run.
        r = be.execute('python -c "print(1 << 2)"')
        assert r.exit_code == 0 and "4" in r.output, r

        # Non-zero exit is surfaced with the exit code.
        r = be.execute('python -c "import sys; sys.exit(3)"')
        assert r.exit_code == 3, r
        assert "Exit code: 3" in r.output, r

    print("restricted_backend: all checks passed")


def test_restricted_backend():
    """Collected by pytest. Without this the file only runs when somebody
    remembers to invoke it by hand, which is how the assertion about `./evil.sh`
    stayed stale after the refusal message was reworded."""
    _run()


if __name__ == "__main__":
    _run()


def test_program_output_is_decoded_as_utf8_not_the_locale(tmp_path):
    """The agent's only channel for finding out whether its change worked.

    `text=True` alone decodes with the platform codepage -- cp1252 on Windows --
    so every em dash in a project's own test output reached the model as three
    characters. The seeds are full of them.
    """
    backend = RestrictedShellBackend(root_dir=str(tmp_path), allow_git=False)
    (tmp_path / "dash.py").write_text(
        'print("the rule — as written")\n', encoding="utf-8")

    result = backend.execute("python dash.py")

    assert "—" in result.output
    assert "â€" not in result.output


def test_an_undecodable_byte_does_not_raise(tmp_path):
    """cp1252 leaves 0x81, 0x8d, 0x8f, 0x90 and 0x9d undefined, and the decode
    was strict -- so a traceback or a `git diff` carrying one of those bytes
    raised inside `execute` rather than returning output."""
    backend = RestrictedShellBackend(root_dir=str(tmp_path), allow_git=False)
    (tmp_path / "raw.py").write_text(
        "import sys\nsys.stdout.buffer.write(bytes([0x81, 0x0a]))\n",
        encoding="utf-8")

    result = backend.execute("python raw.py")

    assert result.exit_code == 0


def test_the_child_reads_project_files_as_utf8(tmp_path):
    """PYTHONUTF8 covers the child's own `open()`, not just its stdout.

    The agent's tests read the project's files, and the seeds are written in
    UTF-8; a child left on cp1252 would fail to decode them or read them wrong.
    """
    backend = RestrictedShellBackend(root_dir=str(tmp_path), allow_git=False)
    (tmp_path / "page.md").write_text("the rule — as written\n", encoding="utf-8")
    (tmp_path / "read.py").write_text(
        "print(open('page.md').read().strip())\n", encoding="utf-8")

    result = backend.execute("python read.py")

    assert "the rule — as written" in result.output
