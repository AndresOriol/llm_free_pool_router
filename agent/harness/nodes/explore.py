"""EXPLORE: find and read code, and report what is there.

Read-only, and the safe default when the orchestrator's reply cannot be parsed.
It is also the move an orchestrator can always justify, which is why the graph
caps consecutive explores (agent/harness/graph.py).

What it contributes to the next node is the paths it turned up: a writer that
knows which file to open does not have to go looking for it.
"""

from __future__ import annotations

import re

from agent.harness.nodes.base import COMMON, Node
from agent.harness.nodes.shared import report_from_text

PROMPT = (f"{COMMON}\nYou explore a codebase and report what you found. You "
          "cannot change anything and you never claim that you did -- observed: "
          "an explore role reporting it had implemented a fix and run the "
          "tests, holding neither an edit tool nor a shell.")

_PATH_RE = re.compile(r"(/[\w./\-]+\.\w+)")


def _paths(result) -> list:
    """Every path this node's tools printed, in the order they appeared."""
    found = []
    for _, out in result.outputs:
        found.extend(_PATH_RE.findall(out))
    return found


def absorb(result, log) -> None:
    log.files("explore", _paths(result))


NODE = Node(
    name="explore",
    prompt=PROMPT,
    tools=("find_files", "search_code", "read_lines", "list_dir"),
    reads=("task",),
    max_rounds=6,
    report=report_from_text,
    absorb=absorb,
)
