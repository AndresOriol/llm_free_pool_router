"""The session graph: who acts next, and what the code refuses to let them do.

One session is one long unattended run over one project (see
docs/design/long-run-harness.md). An orchestrator picks the next worker and
writes its brief; the worker acts; the result is folded into the blackboard and
appended to the journal; repeat until DONE, GIVEUP, or the budget runs out.

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
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from agent.harness.blackboard import Blackboard
from agent.harness.envelope import Brief, Report, parse_brief, parse_report
from agent.harness.record import (STATE_DIR, Git, Journal, Step, Transcript,
                                  append_to_notes, read_notes, replay,
                                  write_rationale, _summarize)
from agent.harness.roles import ORCHESTRATE, REPORTING, ROLES
from agent.harness.runner import Stats, run_role

logger = logging.getLogger("harness")

# Steps cost roughly half a minute each against the real pool, so this is set by
# the scenario timeout rather than by how much work a session could usefully do.
# Raise it with the timeout, not on its own.
MAX_STEPS = 14
# Consecutive explores tolerated once the files are known.
MAX_CONSECUTIVE_EXPLORE = 2

_EXIT_RE = re.compile(r"^exit=(-?\d+)", re.MULTILINE)
_PATH_RE = re.compile(r"(/[\w./\-]+\.\w+)")


class SessionState(TypedDict, total=False):
    """Only what an edge reads. Everything else is a collaborator, not state."""

    action: str          # the worker to run next, or "done" / "giveup"
    brief: Brief         # what that worker is being told
    reviewed: bool       # has a review returned DONE
    applied: bool        # did the last write land an edit
    step: int            # journal step number, continued across a resume
    recent: list         # the roles that ran, for the explore guard
    outcome: str


# ---------------------------------------------------------------------------
# Turning a role's raw result into a report.
# ---------------------------------------------------------------------------

def _exit_code(output: str) -> Optional[int]:
    match = _EXIT_RE.search(output or "")
    return int(match.group(1)) if match else None


def build_report(action: str, result) -> Report:
    """What a role achieved, judged by the right thing for that role.

    A reporting role is taken at its word about what it *found*. An acting role
    is not: its status comes from its tool effects. A model that says "tests
    pass" about a failing run must not be able to end a session, and under a
    review model where nobody reads the code, it would end it convincingly.
    """
    if action in REPORTING:
        return parse_report(result.text)

    evidence, ran_ok = [], None
    for call in result.calls:
        if call["name"] != "run_command":
            continue
        code = _exit_code(call["output"])
        evidence.append([str(call["args"].get("command", "?")), code])
        if code is not None:
            ran_ok = code == 0 if ran_ok is None else (ran_ok and code == 0)

    applied = [out for name, out in result.outputs
               if name in {"replace_in_file", "create_file"} and out.startswith("ok:")]

    if action == "execute":
        status = "DONE" if ran_ok else ("PARTIAL" if evidence else "BLOCKED")
        finding = (f"ran {len(evidence)} command(s); "
                   f"{'all succeeded' if ran_ok else 'something failed'}"
                   ) if evidence else "ran nothing"
    else:
        status = "DONE" if applied else "PARTIAL"
        finding = applied[-1] if applied else (result.text or "nothing applied")[:200]

    # A role that reports INSUFFICIENT_CONTEXT while doing nothing is telling
    # the orchestrator something it needs to hear, so let the text override an
    # inferred PARTIAL -- but never let it override a demonstrated success.
    if status != "DONE" and "INSUFFICIENT_CONTEXT" in (result.text or "").upper():
        return Report(status="INSUFFICIENT_CONTEXT",
                      finding=parse_report(result.text).finding, evidence=evidence)

    return Report(status=status, finding=finding, evidence=evidence)


def _paths(result) -> list:
    found = []
    for _, out in result.outputs:
        found.extend(_PATH_RE.findall(out))
    return found


def absorb(action: str, report: Report, result, bb: Blackboard) -> None:
    """Fold a step into the blackboard. The only channel between roles."""
    if report.finding:
        bb.add_note(f"[{action}] {report.finding}")
    if action == "explore":
        bb.add_files(_paths(result))
    elif action in {"write", "document"}:
        for name, out in result.outputs:
            if name in {"replace_in_file", "create_file"} and out.startswith("ok:"):
                bb.add_edit(out)
    elif action == "execute":
        outputs = [out for name, out in result.outputs if name == "run_command"]
        if outputs:
            bb.set_exec(outputs[-1], _exit_code(outputs[-1]) == 0)
    bb.record(action, f"{report.status}: {report.finding[:80]}")


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

    if action.lower() not in ROLES:
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
        decision = run_role(model, ORCHESTRATE, toolset, bb, config, stats)
        transcript.write("orchestrate", decision)
        brief, action = _veto(parse_brief(decision.text), state, bb)
        if action in {"done", "giveup"}:
            return {"action": action, "brief": brief, "outcome": action}
        return {"action": action, "brief": brief}

    def worker(role):
        def node(state: SessionState) -> dict:
            brief = state["brief"]
            result = run_role(model, role, toolset, bb, config, stats,
                              force_summary=role.reports, brief=brief)
            transcript.write(role.name, result, brief=brief)
            report = build_report(role.name, result)
            absorb(role.name, report, result, bb)

            step = state.get("step", 0) + 1
            journal.append(Step(n=step, action=role.name, goal=brief.goal,
                                status=report.status, finding=report.finding,
                                evidence=report.evidence,
                                context=brief.context, done_when=brief.done_when))

            done = report.status == "DONE"
            update: dict = {"step": step,
                            "recent": (state.get("recent") or []) + [role.name],
                            "applied": role.name == "write" and done}
            if role.name == "review":
                update["reviewed"] = done
            if role.name in {"write", "document"} and done:
                git.commit(f"{role.name}: {(brief.goal or report.finding)[:60]}")
                bb.set_diff(git.diff())
            if role.name == "write" and done:
                # "Check what you just changed" is always the right next move,
                # so it is not worth a model call to be told so.
                update["brief"] = Brief(
                    action="EXECUTE",
                    goal="Check the change that was just applied.",
                    context=report.finding,
                    done_when="A command has run and its exit code is known.")
            return update
        return node

    graph = StateGraph(SessionState)
    graph.add_node("orchestrate", orchestrate)
    for name, role in ROLES.items():
        graph.add_node(name, worker(role))

    graph.add_edge(START, "orchestrate")
    graph.add_conditional_edges(
        "orchestrate",
        lambda state: (END if state["action"] in {"done", "giveup", "exhausted"}
                       else state["action"]),
        {**{name: name for name in ROLES}, END: END})
    # An applied edit runs the code; everything else asks what to do next.
    graph.add_conditional_edges(
        "write",
        lambda state: "execute" if state.get("applied") else "orchestrate",
        {"execute": "execute", "orchestrate": "orchestrate"})
    for name in ROLES:
        if name != "write":
            graph.add_edge(name, "orchestrate")
    return graph.compile()


def mermaid() -> str:
    """The graph's own picture. Documentation that cannot drift from the code."""
    return build(None, {}, Blackboard(task=""), Journal(Path("j")),
                 Transcript(Path("t")), Git(Path(".")), {}, Stats()
                 ).get_graph().draw_mermaid()


# ---------------------------------------------------------------------------
# One session, start to finish.
# ---------------------------------------------------------------------------

def run_session(model, backend, toolset, task: str, workdir: Path, config=None,
                max_steps: int = MAX_STEPS, session_id: str = "") -> tuple:
    """Run one session. Returns (blackboard, stats, outcome, steps)."""
    config = dict(config or {})
    workdir = Path(workdir)
    session_id = session_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    notes = read_notes(workdir)
    full_task = task if not notes else f"{task}\n\n# Project notes\n{notes}"
    bb = Blackboard(task=full_task)

    journal = Journal(workdir / STATE_DIR / "journal.jsonl")
    transcript = Transcript(workdir / STATE_DIR / "steps")
    resumed = replay(journal, bb)
    if resumed:
        logger.info(f"Resuming: {resumed} step(s) replayed from the journal.")

    git = Git(workdir)
    git.start(session_id)
    bb.set_diff(git.diff())

    stats = Stats()
    graph = build(model, toolset, bb, journal, transcript, git, config, stats,
                  max_steps=max_steps)

    # The step budget is enforced in the orchestrate node. This is the backstop
    # for a loop the step counter cannot see -- two supersteps per step, plus
    # headroom for the forced write -> execute edge, which adds a worker without
    # a decision in between.
    budget = dict(config)
    budget["recursion_limit"] = max(2, max_steps - resumed) * 2 + 8

    outcome = "exhausted"
    try:
        final = graph.invoke({"step": resumed, "recent": [], "reviewed": False}, budget)
        outcome = final.get("outcome", "exhausted")
    except Exception as exc:  # noqa: BLE001 - a budget end is not a crash
        if type(exc).__name__ != "GraphRecursionError":
            raise
        # R3: a session that runs out of road says so in writing rather than
        # dying. Everything it learned is on disk already, which is why the
        # journal and the blackboard are not graph state.
        logger.info("Step budget reached; writing the rationale and stopping.")

    steps = journal.read()
    rationale_path = workdir / STATE_DIR / "reports" / f"session-{session_id}.md"
    bb.set_diff(git.diff())
    write_rationale(rationale_path, session_id, full_task, steps, outcome,
                    bb.diff, git.branch)
    append_to_notes(workdir, session_id, outcome, rationale_path,
                    _summarize(steps, outcome))
    git.commit(f"session {session_id}: {outcome}")
    return bb, stats, outcome, steps
