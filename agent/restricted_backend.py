"""A deepagents backend that lets the agent run its OWN tests, but only that.

`FilesystemBackend` (the default jail) exposes no `execute`, so the agent can
write code but never run it -- it can't close its own loop (write a test, run
it, react to the result). `LocalShellBackend` grants an *unrestricted* host
shell, which an unattended small model could misuse (`rm -rf`, network calls,
git pushes).

This backend is the middle ground chosen for the eval loop: it satisfies
`SandboxBackendProtocol` (so the `execute` tool is enabled) but the shell is
constrained so the blast radius is small:

- Commands run with `shell=False`, so `&&`, `|`, `;`, `>`, `$(...)` and other
  shell metacharacters are passed as literal arguments, not interpreted --
  there is no command chaining or redirection to escape through.
- Only an allowlisted program may be launched (`python`/`pytest` by default),
  named bare -- no paths, so `./evil.sh` or `/usr/bin/rm` are rejected.
- The working directory is pinned to `root_dir` and known secret env vars are
  stripped from the child, so the test process doesn't inherit the pool's keys.

This is NOT a true sandbox: `python` is arbitrary code execution, so a
determined/confused payload can still touch the host. It stops the easy
mistakes (stray shell commands, network tools, git), not a malicious script.
For real isolation, run the whole agent inside a container.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import uuid

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol

DEFAULT_ALLOWED = ("python", "python3", "py", "pytest")
DEFAULT_TIMEOUT = 300
MAX_OUTPUT_BYTES = 100_000
# Env vars whose name contains any of these are withheld from the child process.
_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD")


def _child_env() -> dict:
    """Inherit the parent env (PATH etc. are needed to find python) but drop
    anything that looks like a credential, so the test process can't read the
    pool's API keys out of its environment."""
    return {
        k: v for k, v in os.environ.items()
        if not any(marker in k.upper() for marker in _SECRET_MARKERS)
    }


class RestrictedShellBackend(FilesystemBackend, SandboxBackendProtocol):
    """FilesystemBackend jail + an `execute` limited to allowlisted programs."""

    def __init__(self, root_dir, allowed_programs=DEFAULT_ALLOWED,
                 timeout=DEFAULT_TIMEOUT):
        # virtual_mode=True keeps the file tools jailed under root_dir, as before.
        super().__init__(root_dir=root_dir, virtual_mode=True, max_file_size_mb=10)
        self._allowed = set(allowed_programs)
        self._timeout = timeout
        self._sandbox_id = f"restricted-{uuid.uuid4().hex[:8]}"

    @property
    def id(self) -> str:
        return self._sandbox_id

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        if not command or not isinstance(command, str):
            return ExecuteResponse(output="Error: Command must be a non-empty string.",
                                   exit_code=1)
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            return ExecuteResponse(output=f"Error: could not parse command: {exc}",
                                   exit_code=1)
        if not argv:
            return ExecuteResponse(output="Error: empty command.", exit_code=1)

        program = argv[0]
        if "/" in program or "\\" in program or program not in self._allowed:
            return ExecuteResponse(
                output=(f"Error: '{program}' is not allowed. This backend only runs "
                        f"{sorted(self._allowed)} (named bare, no path). Run tests via "
                        f"e.g. 'python -m pytest ...'."),
                exit_code=1,
            )

        effective_timeout = timeout if timeout is not None else self._timeout
        try:
            result = subprocess.run(
                argv,
                check=False,
                shell=False,  # no shell: metacharacters are literal args, not chaining
                capture_output=True,
                stdin=subprocess.DEVNULL,
                text=True,
                timeout=effective_timeout,
                cwd=str(self.cwd),
                env=_child_env(),
            )
        except subprocess.TimeoutExpired:
            return ExecuteResponse(
                output=f"Error: command timed out after {effective_timeout}s.",
                exit_code=124,
            )
        except Exception as exc:  # noqa: BLE001 - return a consistent response
            return ExecuteResponse(output=f"Error executing command "
                                   f"({type(exc).__name__}): {exc}", exit_code=1)

        parts = []
        if result.stdout:
            parts.append(result.stdout)
        if result.stderr:
            parts.extend(f"[stderr] {line}"
                         for line in result.stderr.strip().split("\n"))
        output = "\n".join(parts) if parts else "<no output>"

        truncated = len(output) > MAX_OUTPUT_BYTES
        if truncated:
            output = output[:MAX_OUTPUT_BYTES] + f"\n\n... truncated at {MAX_OUTPUT_BYTES} bytes."
        if result.returncode != 0:
            output = f"{output.rstrip()}\n\nExit code: {result.returncode}"

        return ExecuteResponse(output=output, exit_code=result.returncode,
                               truncated=truncated)
