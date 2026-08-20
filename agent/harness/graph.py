"""The session graph: who acts next, and what the code refuses to let them do.

The nodes themselves are in agent/harness/nodes/, one file each. This file is
only the relationships between them: the edges, and the vetoes.

    START ──▶ orchestrate ──┬──▶ explore  ──┐
                  ▲         ├──▶ write ──▶ execute
                  │         ├──▶ document ─┤
                  │         ├──▶ review ───┤
                  │         └──▶ END       │
                  └─────────────────────────┘

That picture is not maintained by hand -- `mermaid()` below prints it from the
compiled graph, so it cannot drift from the code.

**The orchestrator proposes and the code vetoes.** Every deterministic edge is
both a model call not spent on a decision with one right answer, and a way the
run cannot go wrong. All of them live in `_veto` or in an edge function, so
there is one place to read them:

| Refusal | Why |
| --- | --- |
| `DONE` with nothing executed becomes `EXECUTE` | An unverified "done" is the `stopping` failure wearing a confident face |
| `DONE` with nothing reviewed becomes `REVIEW` | Nobody else is going to look |
| A third consecutive `EXPLORE` becomes `WRITE` | Observed: nine of twelve steps were `explore`, re-reading the same four files. Reading is the move an orchestrator can always justify |
| An unparseable action becomes `EXPLORE` | The read-only worker is the safe default, never one that writes |
| An applied edit goes straight to `EXECUTE` | "Check what you just changed" has one right answer |

**What is state and what is not.** The graph's state holds only what the edges
read: which worker is next, its brief, whether a review has passed, the step
count. The blackboard, the journal, the transcript and git are collaborators the
nodes close over -- they are not merged between branches, they are written to.
Keeping them out of the state also means their contents survive a
`GraphRecursionError`, which is how a session that runs out of budget still
writes its rationale (R3: it never ends silently).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from agent.harness.blackboard import Blackboard
from agent.harness.nodes import ORCHESTRATOR, WORKERS, Stats, absorb, run_node
from agent.harness.protocol import Brief, parse_brief
from agent.harness.record import Git, Journal, Step, Transcript

logger = logging.getLogger("harness")

# Steps cost roughly half a minute each against the real pool, so this is set by
# the scenario timeout rather than by how much work a session could usefully do.
# Raise it with the timeout, not on its own.
MAX_STEPS = 14
# Consecutive explores tolerated once the files are known.
MAX_CONSECUTIVE_EXPLORE = 2


class SessionState(TypedDict, total=False):
    """Only what an edge reads. Everything else is a collaborator, not state."""

    action: str          # the worker to run next, or "done" / "giveup"
    brief: Brief         # what that worker is being told
    reviewed: bool       # has a review returned DONE
    applied: bool        # did the last write land an edit
    step: int            # journal step number, continued across a resume
    recent: list         # the nodes that ran, for the explore guard
    outcome: str


# ---------------------------------------------------------------------------
# The vetoes. Every one of them is a decision the orchestrator does not get.
# ---------------------------------------------------------------------------

def _veto(brief: Brief, state: SessionState, bb: Blackboard) -> tuple:
    """(brief, action) after the deterministic push-backs. See the module table."""
    action = brief.action

    if action == "DONE":
        if not bb.exec_ok:
            bb.record("orchestrate", "DONE refused: nothing has run yet")
            return (Brief(action="EXECUTE",
                          goal="Verify the change actually works.",
                          context=brief.context,
                          done_when="A command has run and its exit code is known."),
                    "execute")
        if not state.get("reviewed"):
            bb.record("orchestrate", "DONE refused: nothing has been reviewed")
            return (Brief(action="REVIEW",
                          goal="Check the change does what was asked.",
                          context=brief.context,
                          done_when="You have judged the change correct or named "
                                    "what is wrong."),
                    "review")
        return brief, "done"

    if action == "GIVEUP":
        return brief, "giveup"

    recent = state.get("recent") or []
    if (action == "EXPLORE" and bb.files
            and recent[-MAX_CONSECUTIVE_EXPLORE:].count("explore")
            >= MAX_CONSECUTIVE_EXPLORE):
        bb.record("orchestrate", "EXPLORE refused: already explored twice")
        return (Brief(action="WRITE",
                      goal="Apply the change the task asks for.",
                      context=(brief.context or "") + "\n" + "\n".join(bb.notes[-2:]),
                      done_when="An edit has been applied to a file."),
                "write")

    if action.lower() not in WORKERS:
        bb.record("orchestrate", f"unknown action {action!r}; exploring")
        return Brief(action="EXPLORE", goal=brief.goal, context=brief.context), "explore"

    return brief, action.lower()


# ---------------------------------------------------------------------------
# The graph.
# ---------------------------------------------------------------------------

def build(model, toolset, bb: Blackboard, journal: Journal,
          transcript: Transcript, git: Git, config: dict, stats: Stats,
          max_steps: int = MAX_STEPS):
    """Compile the session graph. Nodes close over the session's collaborators."""

    def orchestrate(state: SessionState) -> dict:
        # The budget counts *journal steps*, which is what the scenario timeout
        # and the cost figures are reasoned about. LangGraph's recursion_limit
        # counts supersteps and stays a backstop, not the rule.
        if state.get("step", 0) >= max_steps:
            return {"action": "exhausted", "outcome": "exhausted"}
        decision = run_node(model, ORCHESTRATOR, toolset, bb, config, stats)
        transcript.write("orchestrate", decision)
        brief, action = _veto(parse_brief(decision.text), state, bb)
        if action in {"done", "giveup"}:
            return {"action": action, "brief": brief, "outcome": action}
        return {"action": action, "brief": brief}

    def worker(node):
        def run(state: SessionState) -> dict:
            brief = state["brief"]
            result = run_node(model, node, toolset, bb, config, stats,
                              force_summary=node.reports, brief=brief)
            transcript.write(node.name, result, brief=brief)
            # Both of these live in the node's own file: how its raw result
            # becomes a verdict, and what of it the next node gets to see.
            report = node.report(result)
            absorb(node, report, result, bb)

            step = state.get("step", 0) + 1
            journal.append(Step(n=step, action=node.name, goal=brief.goal,
                                status=report.status, finding=report.finding,
                                evidence=report.evidence,
                                context=brief.context, done_when=brief.done_when))

            done = report.status == "DONE"
            update: dict = {"step": step,
                            "recent": (state.get("recent") or []) + [node.name],
                            "applied": node.name == "write" and done}
            if node.name == "review":
                update["reviewed"] = done
            if node.name in {"write", "document"} and done:
                git.commit(f"{node.name}: {(brief.goal or report.finding)[:60]}")
                bb.set_diff(git.diff())
            if node.name == "write" and done:
                # "Check what you just changed" is always the right next move,
                # so it is not worth a model call to be told so.
                update["brief"] = Brief(
                    action="EXECUTE",
                    goal="Check the change that was just applied.",
                    context=report.finding,
                    done_when="A command has run and its exit code is known.")
            return update
        return run

    graph = StateGraph(SessionState)
    graph.add_node("orchestrate", orchestrate)
    for name, node in WORKERS.items():
        graph.add_node(name, worker(node))

    graph.add_edge(START, "orchestrate")
    graph.add_conditional_edges(
        "orchestrate",
        lambda state: (END if state["action"] in {"done", "giveup", "exhausted"}
                       else state["action"]),
        {**{name: name for name in WORKERS}, END: END})
    # An applied edit runs the code; everything else asks what to do next.
    graph.add_conditional_edges(
        "write",
        lambda state: "execute" if state.get("applied") else "orchestrate",
        {"execute": "execute", "orchestrate": "orchestrate"})
    for name in WORKERS:
        if name != "write":
            graph.add_edge(name, "orchestrate")
    return graph.compile()


def mermaid() -> str:
    """The graph's own picture. Documentation that cannot drift from the code."""
    return build(None, {}, Blackboard(task=""), Journal(Path("j")),
                 Transcript(Path("t")), Git(Path(".")), {}, Stats()
                 ).get_graph().draw_mermaid()
