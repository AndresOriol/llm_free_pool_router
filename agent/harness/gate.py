"""The regression gate: what was passing before the session must still pass.

The harness could already refuse a `DONE` that had run nothing and a `DONE` that
had been reviewed by nobody (agent/harness/graph.py). It could not refuse a
`DONE` that had *broken something that used to work*, because nothing in the
session knew what used to work.

Observed, and the reason this exists: on `scenario/ledger/count-and-share` the
project's notes ask for a change that violates an invariant the project's own
tests assert. The session did as it was told, took `pass_to_pass` from 4/4 to
2/4, and reported success -- with `ran_own_tests: false`, having never run the
suite at all. The test it broke was visible, runnable and named
`test_summarise_does_not_modify_the_entries_it_is_given`. Nothing looked.

**Baseline, then compare.** A snapshot is the set of test ids that *fail*. Taken
once before the first edit, and again when the orchestrator wants to finish. A
regression is a test failing now that was not failing then -- which is the right
comparison rather than "everything passes", because a scenario that opens with a
red test is the normal case here, and demanding green would refuse the very fix
the session was asked to make.

**It runs through the agent's own `run_command` tool**, not through subprocess
directly. So it inherits the jail, the allowlist and the timeout, it cannot
reach anything the agent could not reach, and it needs no plumbing of its own.
"""

from __future__ import annotations

import re

# `-q --tb=no` because nothing here reads a traceback: the ids are the signal,
# and the writer gets the real output from its own EXECUTE step. `-rf` is the
# part that matters -- it prints one `FAILED <id>` line per failure, which is
# the whole parse.
COMMAND = "python -m pytest -q --tb=no -rf"

# pytest's exit codes: 0 all passed, 1 some failed. Anything else (2 interrupted,
# 3 internal error, 4 usage error, 5 nothing collected) means the suite did not
# run, and a gate that cannot see the tests must not have an opinion about them.
_USABLE = (0, 1)

_EXIT_RE = re.compile(r"^exit=(-?\d+)", re.MULTILINE)
_FAILED_RE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.MULTILINE)


class Suite:
    """The project's own tests, as a thing the session can compare against.

    Not named `Tests`: pytest tries to collect any class called that, and a
    warning in the suite of a project whose whole subject is running suites is
    the kind of noise that gets ignored until it hides something.
    """

    def __init__(self, run_command, command: str = COMMAND):
        # `run_command` is the agent's tool, already bound to the backend.
        self._run = run_command
        self._command = command
        self.baseline: frozenset | None = None
        self.available = False

    def snapshot(self) -> frozenset | None:
        """Test ids failing right now, or None if the suite could not be run."""
        try:
            output = str(self._run.invoke({"command": self._command}))
        except Exception:  # noqa: BLE001 - a gate must never end a session
            return None
        match = _EXIT_RE.search(output)
        if match is None or int(match.group(1)) not in _USABLE:
            return None
        return frozenset(_FAILED_RE.findall(output))

    def take_baseline(self) -> frozenset | None:
        """Record the starting state. Call once, before anything is edited."""
        self.baseline = self.snapshot()
        self.available = self.baseline is not None
        return self.baseline

    def regressions(self) -> list:
        """Tests failing now that were passing at baseline, newest verdict wins.

        Empty when the gate has no baseline to compare against: an unusable
        suite is a reason to stay quiet, never a reason to block a session that
        may be perfectly fine.
        """
        if not self.available:
            return []
        now = self.snapshot()
        if now is None:
            return []
        return sorted(now - self.baseline)

    def describe(self) -> str:
        """One line for the log, so the nodes can see what the gate knows."""
        if not self.available:
            return ""
        if self.baseline:
            return (f"Baseline: {len(self.baseline)} test(s) already failing before "
                    f"any change: {', '.join(sorted(self.baseline))}. Fixing those "
                    f"is the job; breaking anything else is a regression.")
        return "Baseline: the project's test suite passes completely. Keep it that way."
