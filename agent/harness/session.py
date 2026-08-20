"""One session, start to finish: wiring, budget, resume, and the account it leaves.

The graph decides what happens (agent/harness/graph.py) and the record package
holds what survives it (agent/harness/record/). This is the file that puts the
two together for one run over one workdir -- and it is where the guarantee that
a session never ends silently is actually implemented.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from agent.harness.gate import Suite
from agent.harness.log import Log
from agent.harness.graph import MAX_STEPS, build
from agent.harness.nodes import Stats
from agent.harness.record import (STATE_DIR, TRANSCRIPT_DIR, Git, Journal,
                                  Transcript, append_to_notes, read_notes,
                                  replay, summarize, write_rationale)

logger = logging.getLogger("harness")

__all__ = ["MAX_STEPS", "run_session"]


def run_session(model, toolset, task: str, workdir: Path, config=None,
                max_steps: int = MAX_STEPS, session_id: str = "") -> tuple:
    """Run one session. Returns (log, stats, outcome, steps)."""
    config = dict(config or {})
    workdir = Path(workdir)
    session_id = session_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    notes = read_notes(workdir)
    full_task = task if not notes else f"{task}\n\n# Project notes\n{notes}"
    log = Log(task=full_task)

    journal = Journal(workdir / STATE_DIR / "journal.jsonl")
    transcript = Transcript(workdir / STATE_DIR / TRANSCRIPT_DIR)
    resumed = replay(journal, log)
    if resumed:
        logger.info(f"Resuming: {resumed} step(s) replayed from the journal.")

    git = Git(workdir)
    git.start(session_id)
    log.diff(git.diff())

    # Before anything is edited: what already fails, so that what this session
    # breaks can be told apart from what it was asked to fix
    # (agent/harness/gate.py). A suite that cannot be run leaves the gate quiet
    # rather than blocking, so this is safe on a project that has no tests.
    gate = Suite(toolset["run_command"]) if "run_command" in toolset else None
    if gate is not None:
        gate.take_baseline()
        described = gate.describe()
        if described:
            logger.info(described)
            log.note("gate", described)
        else:
            logger.info("No usable test suite; the regression gate is off.")

    stats = Stats()
    graph = build(model, toolset, log, journal, transcript, git, config, stats,
                  max_steps=max_steps, gate=gate)

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
        # journal and the log are not graph state.
        logger.info("Step budget reached; writing the rationale and stopping.")

    steps = journal.read()
    rationale_path = workdir / STATE_DIR / "reports" / f"session-{session_id}.md"
    # The rationale counts the files the session touched, so it gets the whole
    # diff. The log gets the same diff clipped to what a node can afford to
    # read; counting from that clipped copy undercounted a long session.
    diff = git.diff()
    log.diff(diff)
    write_rationale(rationale_path, session_id, full_task, steps, outcome,
                    diff, git.branch)
    append_to_notes(workdir, session_id, outcome, rationale_path,
                    summarize(steps, outcome))
    git.commit(f"session {session_id}: {outcome}")
    return log, stats, outcome, steps
