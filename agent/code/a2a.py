"""The coding agent as an addressable agent: its card, and the handler behind it.

The server half of the protocol for `agent/code`, and the exact counterpart of
[agent/explore/a2a.py](../explore/a2a.py). It changes nothing about how the
coding agent works -- same prompt, same jail, same pool, same session -- and
only gives it an address and a way to say what came back.

**What a coding task delivers is not a file, it is a commit.** That is the one
real difference from the explorer, whose notes map onto `Artifact` the way the
spec intended ([15.1](../../docs/15-explorer.md#151-what-it-is-for)). A session
commits its own work incrementally on its own branch
([backend.py](../runtime/backend.py) allows `add`/`commit`/`branch` and refuses
`merge`/`push`), so the deliverable is a range of commits in a workspace that
outlives the run. The handler therefore reports git state -- branch, head, and
what moved since the task started -- as a `DataPart`, because a caller that
cannot name the commit cannot review it, and "read the closing message" is not
a review.

A workspace that is not a git repository is a normal, supported case: the
`DataPart` then carries `{"git": false}` and the files are simply on disk where
the caller mounted them ([18.4](../../docs/18-serving.md#184-binding-an-agent-to-a-repository-or-a-filesystem)).
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

from agent.protocol.types import (AgentCard, AgentSkill, Artifact, DataPart,
                                  Message, Task, TaskState)

logger = logging.getLogger("harness.code")

CARD = AgentCard(
    name="code",
    version="0.1.0",
    description=("Works a project unattended on its own branch: reads the "
                 "tree, edits files, runs `python`/`pytest`, and commits as it "
                 "goes. It never merges, pushes, or touches a remote."),
    skills=[
        AgentSkill(
            id="work_project",
            name="Work a project",
            description=("Carry out a development task against the bound "
                         "workspace — implement a change, fix a failing test, "
                         "refactor a module — and commit the result."),
            tags=["code", "refactor", "tests", "git"],
            examples=[
                "Read NOTES.md and do what the newest feedback asks for.",
                "The suite in tests/test_parser.py fails on empty input. Find "
                "out why, fix it, and add a regression test.",
            ],
        ),
    ],
)

# `git` is asked for state, never for changes: every command below is a read.
# The agent's own commits are made inside the jail by the agent, which is the
# only thing that should be writing history here.
_GIT_TIMEOUT = 30


def _git(workdir: Path, *args: str) -> Optional[str]:
    """One read-only git command, or None if it could not be answered.

    None rather than an exception for every failure mode -- not a repository,
    no git on PATH, an empty repository with no HEAD yet. Reporting git state
    is how the caller reviews the work; failing to report it must not fail work
    that is already done and already committed.
    """
    try:
        done = subprocess.run(("git", *args), cwd=str(workdir),
                              capture_output=True, text=True,
                              timeout=_GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug(f"git {' '.join(args)} failed: {exc!r}")
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip()


def _head(workdir: Path) -> Optional[str]:
    return _git(workdir, "rev-parse", "HEAD")


def _git_state(workdir: Path, before: Optional[str]) -> dict:
    """Branch, head, and what moved since `before`. The reviewable summary.

    `before` is the head this task started from. Without it the diff would be
    against the working tree only, which reports uncommitted noise and misses
    the commits that are the actual deliverable.
    """
    head = _head(workdir)
    if head is None:
        return {"git": False}

    state: dict = {"git": True, "head": head,
                   "branch": _git(workdir, "rev-parse", "--abbrev-ref", "HEAD")}

    if before:
        state["head_before"] = before
        count = _git(workdir, "rev-list", "--count", f"{before}..{head}")
        state["commits"] = int(count) if (count or "").isdigit() else 0
        changed = _git(workdir, "diff", "--name-only", before, head) or ""
        state["files_changed"] = [p for p in changed.splitlines() if p.strip()]

    # Anything the agent left uncommitted. Worth naming: a run that edited
    # files and never committed them looks identical to one that did nothing,
    # in every field above.
    dirty = _git(workdir, "status", "--porcelain")
    if dirty:
        state["uncommitted"] = [line[3:] for line in dirty.splitlines()
                                if line.strip()]
    return state


def make_handler(model, workdir: Path, *, floor: int, members: int,
                 recursion_limit: int, allow_shell: bool = False,
                 transport=None, trace_path: Optional[Path] = None):
    """The handler the registry serves for `code`.

    Takes the workdir once, at wiring time, and closes over it -- the same
    shape as the explorer's, so a registry holds the two without knowing which
    is which. The server builds one handler per task because each task names
    its own workspace ([serve/runner.py](../serve/runner.py)).

    `transport` is the peers transport this agent delegates *through*, and is
    unrelated to whatever transport is calling it. Passing None gives the agent
    no `delegate` tool, which is what `AGENT_PEERS=` means
    ([16](../../docs/16-agent-protocol.md)).
    """
    from agent.code.session import run_session

    workdir = Path(workdir)

    def handle(task: Task) -> Task:
        request = task.history[-1].text if task.history else ""
        if not request:
            return task.advance(TaskState.REJECTED, Message.agent(
                "The request was empty; there is nothing to do."))

        before = _head(workdir)
        final, _ = run_session(model, request, workdir,
                               config={"recursion_limit": recursion_limit},
                               floor=floor, members=members,
                               allow_shell=allow_shell, transport=transport,
                               trace_path=trace_path)

        state = _git_state(workdir, before)
        task.artifacts.append(Artifact(
            name="workspace",
            description=f"The state of the workspace after task {task.id[:8]}.",
            parts=[DataPart({"workspace": str(workdir), **state})],
        ))

        closing = _closing_text(final)
        message = Message.agent(closing, task_id=task.id,
                                context_id=task.context_id)
        task.history.append(message)
        return task.advance(TaskState.COMPLETED, message)

    return handle


def _closing_text(final) -> str:
    """The session's last message, flattened and clipped.

    The same treatment `agent/explore/a2a.py` gives its own, and for the same
    reason: the deliverable is the commit, not the sign-off, and an unclipped
    ramble would spend the caller's context on the part of the run nobody was
    meant to read.
    """
    messages = (final or {}).get("messages") or []
    if not messages:
        return "The session ended without a closing message."
    text = getattr(messages[-1], "content", "")
    if isinstance(text, list):  # content blocks
        text = " ".join(str(b.get("text", "")) for b in text
                        if isinstance(b, dict))
    text = str(text).strip()
    return text[:2000] + ("…" if len(text) > 2000 else "")
