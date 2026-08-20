"""ORCHESTRATE: choose who acts next, and tell them what they need to know.

The hub. It is the only node routed to a wide-context member, because it is the
only one that sees everything -- and everything the workers see, it had to
choose to write down for them (agent/harness/protocol.py).

It holds no tools, on purpose: the call that decides what happens next must not
be able to make anything happen. Its reply is a Brief, not a Report, so the
`report` default here is never used; the graph parses its text with
`parse_brief` and then gets to refuse it (see the veto table in
agent/harness/graph.py).
"""

from __future__ import annotations

from agent.harness.nodes.base import WIDE, Node

PROMPT = ("You direct a team of coding agents working on one project. You have "
          "no tools; the others do the work. Your only job is to choose who "
          "acts next and to tell them what they need to know.")

INSTRUCTION = (
    "Choose the next action and write the brief for it. Reply in "
    "exactly this form:\n\n"
    "ACTION: EXPLORE | WRITE | EXECUTE | DOCUMENT | REVIEW | DONE | GIVEUP\n"
    "GOAL: <one sentence: what this step must achieve>\n"
    "CONTEXT: <the facts that step needs, copied out in full -- it "
    "cannot see anything you do not write here>\n"
    "DONE_WHEN: <how that step knows it has finished>\n\n"
    "EXPLORE finds and reads code. WRITE changes it. EXECUTE runs "
    "things to check whether it works. DOCUMENT updates the "
    "documentation to match the change. REVIEW checks the work is "
    "right before finishing. DONE only after a successful run and a "
    "review.")

NODE = Node(
    name="orchestrate",
    prompt=PROMPT,
    tools=(),
    reads=("task", "files", "notes", "edits", "diff", "exec", "steps"),
    max_rounds=1,
    min_context=WIDE,
    instruction=INSTRUCTION,
)
