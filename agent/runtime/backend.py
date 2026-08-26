"""The filesystem jail, plus enough `execute` for the agent to run its own tests.

Built on deepagents' `FilesystemBackend`, which is where the path-jailed
`glob`/`grep`/`read`/`write` come from. Both agents sit on it -- the coding
agent (agent/deep) and the web explorer (agent/explore) -- and both take the
agent loop from deepagents as well.

`FilesystemBackend` alone exposes no `execute`, so an agent on it can write code
but never run it -- it cannot close its own loop (write a test, run it, react to
the result). `LocalShellBackend` grants an *unrestricted* host shell, which an
unattended small model could misuse (`rm -rf`, network calls, git pushes).

This backend is the middle ground: it satisfies
`SandboxBackendProtocol` (so the `execute` tool is enabled) but the shell is
constrained so the blast radius is small:

- Commands run with `shell=False`, so `&&`, `|`, `;`, `>`, `$(...)` and other
  shell metacharacters are never interpreted -- there is no command chaining or
  redirection to escape through. They are also *refused* rather than passed
  through as literal arguments: silently accepting shell syntax that does
  nothing cost one run 900 seconds in heredocs that hung until the timeout.
- Only an allowlisted program may be launched (`python`/`pytest` by default),
  named bare -- no paths, so `./evil.sh` or `/usr/bin/rm` are rejected.
- The working directory is pinned to `root_dir` and known secret env vars are
  stripped from the child, so the test process doesn't inherit the pool's keys.

Two capabilities are opt-in, because a long-running session needs them and a
one-shot task does not:

- `allow_git=True` adds `git`, filtered by subcommand. A session commits its own
  work incrementally on its own branch, so the human's gate is the *merge* --
  which is why `merge`, `push`, `rebase`, `reset` and `clean` are refused.
- `allow_shell=True` drops the allowlist entirely and runs the command through
  the platform shell. The Executor role wants this: deciding what to run to
  convince yourself code works is judgement, and it is hobbled by a two-program
  allowlist.

This is NOT a true sandbox: `python` is arbitrary code execution, so a
determined/confused payload can still touch the host even in the default mode.
It stops the easy mistakes (stray shell commands, network tools, git), not a
malicious script. With `allow_shell=True` it stops nothing at all -- that mode
is an admission that the boundary was always a container's job, not this
class's. For real isolation, run the whole agent inside a container.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import uuid

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol

DEFAULT_ALLOWED = ("python", "python3", "py", "pytest")

# Tokens that only mean anything to a shell. With shell=False they arrive as
# literal arguments, which is safe and useless -- see _refusal.
_SHELL_OPERATORS = frozenset({"|", "||", "&&", ";", "&", ">", ">>", "<", "2>"})
DEFAULT_TIMEOUT = 300
MAX_OUTPUT_BYTES = 100_000
# Env vars whose name contains any of these are withheld from the child process.
_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD")

# Git subcommands a session may run. Everything here is additive and local: no
# allowed command can destroy committed work, and none of them touches a remote.
# The human's gate is the merge, so `merge` is precisely what the agent must not
# have (docs/design/long-run-harness.md#41-git).
GIT_ALLOWED_SUBCOMMANDS = ("status", "diff", "log", "add", "commit", "branch",
                           "rev-parse", "show", "checkout")
# Refused wholesale rather than conditionally. `reset` and `clean` exist to
# throw work away, and a flag-by-flag allowlist is one parsing bug away from
# permitting the thing it was written to forbid.
_GIT_DENIED_FLAGS = ("--force", "-f", "--hard", "--delete", "-D")


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
                 timeout=DEFAULT_TIMEOUT, allow_git=False, allow_shell=False):
        # virtual_mode=True keeps the file tools jailed under root_dir, as before.
        super().__init__(root_dir=root_dir, virtual_mode=True, max_file_size_mb=10)
        self._allowed = set(allowed_programs)
        if allow_git:
            self._allowed.add("git")
        self._allow_shell = allow_shell
        self._timeout = timeout
        self._sandbox_id = f"restricted-{uuid.uuid4().hex[:8]}"

    def _refusal(self, argv) -> str:
        """Why this command is not allowed to run, or '' if it may.

        Split out from execute() so the policy is testable without launching a
        process -- the whole point of an allowlist is that it can be checked.
        """
        program = argv[0]
        if "/" in program or "\\" in program:
            return (f"'{program}' names a path. Programs must be named bare, "
                    f"e.g. 'python -m pytest'.")
        if not self._allowed:
            # An agent configured with no programs at all -- the explorer, whose
            # job is reading and writing, not running. Saying "only runs []" to
            # a model reads as a bug it should work around, and it will try.
            return ("there is no shell here: this agent runs no programs at "
                    "all. Use the file tools, and the web tools if you have "
                    "them; there is nothing to execute.")
        if program not in self._allowed:
            return (f"'{program}' is not allowed. This backend only runs "
                    f"{sorted(self._allowed)} (named bare, no path).")

        # Shell syntax with no shell to interpret it. Refused rather than passed
        # through as literal arguments: a run spent 900 of its 1,050 seconds on
        # three `python - <<'PY'` heredocs, each hanging until the 300s timeout,
        # and the deterministic "run the code after an edit" step returned
        # nothing about the code. Being told is cheap; hanging is not.
        for token in argv[1:]:
            if token.startswith("<<") or token in _SHELL_OPERATORS:
                return (f"'{token}' is shell syntax and there is no shell here, "
                        f"so it cannot be interpreted. No heredocs, pipes, "
                        f"chaining or redirection. To run code that is not in a "
                        f"file, use `python -c \"...\"`; for anything longer, "
                        f"write it with create_file and run `python <file>`.")
            # `python -` reads the script from stdin, and stdin is /dev/null
            # here. Depending on the platform that either does nothing or hangs
            # until the timeout; neither is what the caller wanted.
            if token == "-":
                return ("`-` reads the script from stdin, and this backend gives "
                        "the process no stdin. Use `python -c \"...\"` for a "
                        "one-liner, or create_file plus `python <file>`.")

        if program != "git":
            return ""

        subcommand = next((a for a in argv[1:] if not a.startswith("-")), "")
        if subcommand not in GIT_ALLOWED_SUBCOMMANDS:
            return (f"git {subcommand or '<none>'} is not allowed. Allowed: "
                    f"{', '.join(GIT_ALLOWED_SUBCOMMANDS)}. You cannot merge, "
                    f"push, rebase, reset or clean -- a human merges your branch.")
        # Creating a branch is additive; switching to an existing one abandons
        # uncommitted work on the current branch, which is a way to lose a
        # session's output without any command that looks destructive.
        if subcommand == "checkout" and "-b" not in argv:
            return "git checkout is only allowed as 'git checkout -b <new-branch>'."
        denied = [a for a in argv[1:] if a in _GIT_DENIED_FLAGS]
        if denied:
            return f"git flag(s) {denied} are not allowed."
        return ""

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

        if not self._allow_shell:
            refusal = self._refusal(argv)
            if refusal:
                return ExecuteResponse(output=f"Error: {refusal}", exit_code=1)

        effective_timeout = timeout if timeout is not None else self._timeout
        try:
            result = subprocess.run(
                command if self._allow_shell else argv,
                check=False,
                # With allow_shell the platform shell interprets the command --
                # pipes, chaining and redirection all work, and so does anything
                # else. See the module docstring: that mode is a container's job
                # to contain, not this class's.
                shell=self._allow_shell,
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
