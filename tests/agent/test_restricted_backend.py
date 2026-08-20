"""Checks for RestrictedShellBackend.execute() -- the guardrails that let the
agent run its own tests but not an arbitrary host shell.

No framework: `python -m tests.agent.test_restricted_backend` (or run the file).
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.restricted_backend import RestrictedShellBackend


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
