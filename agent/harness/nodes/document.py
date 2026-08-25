"""DOCUMENT: keep the documentation true to the code that was just written.

Sees the session's diff rather than the actors' account of it, so it needs
breadth -- hence the context floor. Like WRITE it is judged by the edits it
lands, and its edits are committed the same way, so a documentation change is a
reviewable commit rather than an afterthought.
"""

from __future__ import annotations

from agent.harness.nodes.base import COMMON, WIDE, Node
from agent.harness.nodes.shared import applied_edits, report_from_edits

PROMPT = (f"{COMMON}\nYou keep documentation true to the code. You read the "
          "change that was made and update the docs that the change makes "
          "wrong. You never change code.")


def absorb(result, log) -> None:
    for edit in applied_edits(result):
        log.edit("document", edit)


NODE = Node(
    name="document",
    prompt=PROMPT,
    tools=("read_lines", "find_files", "replace_in_file", "create_file"),
    reads=("task", "diff", "edits"),
    max_rounds=6,
    reports=False,
    min_context=WIDE,
    report=report_from_edits,
    absorb=absorb,
)
