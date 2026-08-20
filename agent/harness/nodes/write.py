"""WRITE: apply the code change, exactly as briefed.

An acting node: judged by the edits the backend confirms, never by what it says
it did. A write that lands an edit goes straight to EXECUTE without a decision
in between -- "check what you just changed" has one right answer, so the graph
makes that edge rather than paying a model call to be told so.
"""

from __future__ import annotations

from agent.harness.nodes.base import COMMON, Node
from agent.harness.nodes.shared import applied_edits, report_from_edits

PROMPT = (f"{COMMON}\nYou apply code changes, exactly as briefed. You do not "
          "run tests and you do not explore.")


def absorb(result, bb) -> None:
    for edit in applied_edits(result):
        bb.add_edit(edit)


NODE = Node(
    name="write",
    prompt=PROMPT,
    # read_lines earns its schema here: `replace_in_file` needs `old_text` to
    # match the file exactly, and a writer that cannot look reports BLOCKED --
    # observed, verbatim: "no tool for reading files is provided".
    tools=("read_lines", "replace_in_file", "create_file"),
    sections=("task", "files"),
    max_rounds=4,
    reports=False,
    report=report_from_edits,
    absorb=absorb,
)
