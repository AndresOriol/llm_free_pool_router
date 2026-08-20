"""EXECUTE: decide what to run to find out whether the code works, and run it.

The only node that produces evidence, and evidence is what the session's
rationale is built from: a claim that cannot point at a command and an exit code
did not happen (agent/harness/record/rationale.py). It is also the reason a
`DONE` can be refused -- an unverified "done" is the `stopping` failure wearing
a confident face.

Its status comes from exit codes rather than from its own account. A model that
says "tests pass" about a failing run must not be able to end a session.
"""

from __future__ import annotations

import re

from agent.harness.nodes.base import COMMON, Node
from agent.harness.protocol import Report
from agent.harness.nodes.shared import honour_insufficient

PROMPT = (f"{COMMON}\nYou decide what to run to find out whether the code "
          "works, and you run it. Prefer the project's own tests. When they "
          "do not cover the question, write a small throwaway script and run "
          "that. You never edit the project's own files.")

_EXIT_RE = re.compile(r"^exit=(-?\d+)", re.MULTILINE)


def exit_code(output: str):
    """The exit code `run_command` printed, or None when it printed none."""
    match = _EXIT_RE.search(output or "")
    return int(match.group(1)) if match else None


def report(result) -> Report:
    """Judged by what ran and what it returned, not by what was claimed."""
    evidence, ran_ok = [], None
    for call in result.calls:
        if call["name"] != "run_command":
            continue
        code = exit_code(call["output"])
        evidence.append([str(call["args"].get("command", "?")), code])
        if code is not None:
            ran_ok = code == 0 if ran_ok is None else (ran_ok and code == 0)

    status = "DONE" if ran_ok else ("PARTIAL" if evidence else "BLOCKED")
    finding = (f"ran {len(evidence)} command(s); "
               f"{'all succeeded' if ran_ok else 'something failed'}"
               ) if evidence else "ran nothing"
    return honour_insufficient(
        Report(status=status, finding=finding, evidence=evidence), result)


def absorb(result, log) -> None:
    outputs = [out for name, out in result.outputs if name == "run_command"]
    if outputs:
        log.ran("execute", outputs[-1], exit_code(outputs[-1]) == 0)


NODE = Node(
    name="execute",
    prompt=PROMPT,
    tools=("run_command", "create_file"),
    reads=("task", "edits"),
    max_rounds=3,
    reports=False,
    report=report,
    absorb=absorb,
)
