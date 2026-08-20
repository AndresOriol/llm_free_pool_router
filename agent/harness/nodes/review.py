"""REVIEW: judge the change before it is called done.

Nobody else is going to look, which is why the graph refuses a `DONE` that has
not been through here. It reads the diff, so it needs breadth; it holds only
read tools, so its verdict cannot quietly become an edit.

It contributes nothing beyond the note every node contributes -- its whole value
is the text, and the graph reads its status directly to decide whether the
session may end.
"""

from __future__ import annotations

from agent.harness.nodes.base import COMMON, WIDE, Node
from agent.harness.nodes.shared import report_from_text

PROMPT = (f"{COMMON}\nYou review a change before it is called done. You judge "
          "whether it does what the task asked, not whether it is elegant. You "
          "never change anything.")

NODE = Node(
    name="review",
    prompt=PROMPT,
    tools=("read_lines", "search_code"),
    sections=("task", "diff", "notes", "exec"),
    max_rounds=3,
    min_context=WIDE,
    report=report_from_text,
)
