"""WRITE: apply the code change, exactly as briefed.

An acting node: judged by the edits the backend confirms, never by what it says
it did. A write that lands an edit goes straight to EXECUTE without a decision
in between -- "check what you just changed" has one right answer, so the graph
makes that edge rather than paying a model call to be told so.

**It reads the evidence against its own work.** This node used to declare
`("task", "files")` and nothing else, which made it the most context-starved
node in the harness while being the only one that changes anything. It could
not see the traceback of the test its last edit broke (`exec`), what REVIEW
said was wrong (`notes`), what it had already applied (`edits`), or the diff
it was adding to (`diff`). All of that reached it only if the orchestrator --
which has never read a file -- retyped it into CONTEXT by hand. Observed: a
session whose writer returned "nothing applied" and was then sent back to
EXPLORE five more times, because the fix loop had no way to close.
"""

from __future__ import annotations

from agent.harness.nodes.base import COMMON, Node
from agent.harness.nodes.shared import applied_edits, report_from_edits

PROMPT = (f"{COMMON}\nYou apply code changes, exactly as briefed. You do not "
          "run tests and you do not explore.\n"
          "You never edit a test so that it agrees with your change. A test that "
          "contradicts what you were asked to do is a fact about the task, not "
          "an obstacle: apply the part that does not break it, and say in "
          "FINDING which test disagrees and why. Observed on this node -- told "
          "to set a field the suite forbids, it deleted the assertion and "
          "renamed the test around it.")


def absorb(result, log) -> None:
    for edit in applied_edits(result):
        log.edit("write", edit)


NODE = Node(
    name="write",
    prompt=PROMPT,
    # read_lines earns its schema here: `replace_in_file` needs `old_text` to
    # match the file exactly, and a writer that cannot look reports BLOCKED --
    # observed, verbatim: "no tool for reading files is provided".
    tools=("read_lines", "replace_in_file", "create_file"),
    # The narrowest thing that still closes the fix loop: what it is changing
    # (files), what is known (notes), what it already did (edits, diff), and
    # how that went (exec). Ordered so the failing output sits nearest the ask.
    reads=("task", "files", "notes", "edits", "diff", "exec"),
    max_rounds=8,
    reports=False,
    report=report_from_edits,
    absorb=absorb,
)
