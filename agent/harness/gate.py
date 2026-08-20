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

**The baseline is the set of tests that pass**, not the set that fails. Both
readings catch a test that breaks; only this one catches a test that *stops
existing*. Measured, on the first run of this gate: told to set a field the
suite forbids, the session deleted the assertion and renamed the test around it
--

    -def test_summarise_does_not_modify_the_entries_it_is_given():
    +def test_summarise_adds_month_total_to_each_entry():

-- which leaves nothing failing, so a gate comparing failures sees a clean run.
Comparing what passes turns a rewritten test into a missing one, which is what
it is.

A test that was already failing at baseline is not held against the session:
a scenario that opens red is the normal case here, and demanding green would
refuse the very fix the session was asked to make.

**It runs through the agent's own `run_command` tool**, not through subprocess
directly. So it inherits the jail, the allowlist and the timeout, it cannot
reach anything the agent could not reach, and it needs no plumbing of its own.
"""

from __future__ import annotations

import re

# Two cheap runs rather than one clever one. `--collect-only` is the half that
# knows a test exists at all, which is the half that catches a test edited out
# of the way; `-rf` names the failures. Both are quiet enough to survive the
# tool's output clipping on any suite this harness is pointed at.
COLLECT = "python -m pytest --collect-only -q"
RUN = "python -m pytest -q --tb=no -rf"

# pytest's exit codes: 0 all passed, 1 some failed. Anything else (2 interrupted,
# 3 internal error, 4 usage error, 5 nothing collected) means the suite did not
# run, and a gate that cannot see the tests must not have an opinion about them.
_USABLE = (0, 1)

_EXIT_RE = re.compile(r"^exit=(-?\d+)", re.MULTILINE)
_FAILED_RE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.MULTILINE)
# A collected id is `path::name`, one per line, before the trailing summary.
_ID_RE = re.compile(r"^(\S+::\S+)\s*$", re.MULTILINE)


class Suite:
    """The project's own tests, as a thing the session can compare against.

    Not named `Tests`: pytest tries to collect any class called that, and a
    warning in the suite of a project whose whole subject is running suites is
    the kind of noise that gets ignored until it hides something.
    """

    def __init__(self, run_command):
        # `run_command` is the agent's tool, already bound to the backend.
        self._run = run_command
        self.baseline: frozenset | None = None
        self.available = False

    def _execute(self, command: str) -> str | None:
        """Command output, or None if it did not run usefully."""
        try:
            output = str(self._run.invoke({"command": command}))
        except Exception:  # noqa: BLE001 - a gate must never end a session
            return None
        match = _EXIT_RE.search(output)
        if match is None or int(match.group(1)) not in _USABLE:
            return None
        return output

    def snapshot(self) -> frozenset | None:
        """The test ids passing right now, or None if the suite could not run."""
        collected = self._execute(COLLECT)
        if collected is None:
            return None
        ids = frozenset(_ID_RE.findall(collected))
        if not ids:
            return None
        run = self._execute(RUN)
        if run is None:
            return None
        return ids - frozenset(_FAILED_RE.findall(run))

    def take_baseline(self) -> frozenset | None:
        """Record the starting state. Call once, before anything is edited."""
        self.baseline = self.snapshot()
        self.available = bool(self.baseline)
        return self.baseline

    def regressions(self) -> list:
        """Tests that passed at baseline and do not pass now.

        Covers both halves of the same failure: a test that now fails, and a
        test that is no longer there to fail. Empty when there is no baseline
        to compare against -- an unusable suite is a reason to stay quiet, never
        a reason to block a session that may be perfectly fine.
        """
        if not self.available:
            return []
        now = self.snapshot()
        if now is None:
            return []
        return sorted(self.baseline - now)

    def describe(self) -> str:
        """One line for the log, so the nodes can see what the gate knows."""
        if not self.available:
            return ""
        return (f"Before this session started, {len(self.baseline)} test(s) passed. "
                f"They must all still pass, and still exist, when it finishes. "
                f"Rewriting or deleting a test counts as breaking it.")
