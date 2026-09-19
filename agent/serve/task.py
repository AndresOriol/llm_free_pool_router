"""One served run: what was asked, where, and what came back.

A run outlives its request -- hours against a platform that kills a connection
in minutes ([Why request/response platforms cannot host it](../../docs/operations/deployment.md#why-requestresponse-platforms-cannot-host-it))
-- so submitting has to answer immediately with an id the caller polls. That is
the only reason this type exists: something has to hold the run between the two
requests.

It is deliberately not a protocol. There was one here -- A2A's `Task`, `Message`
and `Artifact`, with a card per agent and a handler beside every session -- and
it is gone with the rest of it ([Delegation](../../docs/agents/delegation.md)).
An agent is a command; the server queues one, runs it, and keeps what it
printed.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("harness.serve")

# The states a served run passes through. Terminal ones are the answer to
# "should I keep polling".
QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELED = "canceled"
TERMINAL = (DONE, FAILED, CANCELED)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Task:
    """A queued or finished agent run."""

    agent: str
    request: str
    workspace: str
    workdir: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: str = QUEUED
    # Everything the command printed, final message first
    # ([The verdict comes from git, not from the delegate](../../docs/agents/delegation.md#the-verdict-comes-from-git-not-from-the-delegate)).
    # Not summarised here: the command already leads with its own answer, and a
    # server that paraphrased it would be one more thing to keep in step.
    output: str = ""
    exit_code: Optional[int] = None
    error: str = ""
    created: str = field(default_factory=now)
    started: str = ""
    finished: str = ""
    metadata: dict = field(default_factory=dict)

    @property
    def done(self) -> bool:
        return self.state in TERMINAL

    def advance(self, state: str, *, output: str = "", error: str = "",
                exit_code: Optional[int] = None) -> "Task":
        self.state = state
        if state == RUNNING:
            self.started = now()
        elif state in TERMINAL:
            self.finished = now()
        if output:
            self.output = output
        if error:
            self.error = error
        if exit_code is not None:
            self.exit_code = exit_code
        return self

    def to_dict(self) -> dict:
        return {"id": self.id, "agent": self.agent, "state": self.state,
                "workspace": self.workspace, "workdir": self.workdir,
                "request": self.request, "output": self.output,
                "exitCode": self.exit_code, "error": self.error,
                "created": self.created, "started": self.started,
                "finished": self.finished, "metadata": self.metadata}


class TaskStore:
    """Every task this process has seen, and optionally a copy on disk.

    In memory because polling has to answer for a task the caller already holds
    an id for. On disk because a served run spends real free-tier quota
    unattended, and the account a human reads afterwards should say what was
    asked and what came back
    ([The record: one run tree](../../docs/evaluation/observability.md#the-record-one-run-tree)).

    The directory is the *record's*, never the workdir: the coding agent commits
    its workdir, and a server log committed into the project under review is
    noise in every diff it produces afterwards.
    """

    def __init__(self, directory: Optional[Path] = None) -> None:
        self._tasks: dict[str, Task] = {}
        self.directory = Path(directory) if directory else None
        if self.directory:
            self.directory.mkdir(parents=True, exist_ok=True)

    def put(self, task: Task) -> Task:
        self._tasks[task.id] = task
        if self.directory and task.done:
            path = self.directory / f"{task.id}.json"
            try:
                path.write_text(json.dumps(task.to_dict(), indent=2,
                                           ensure_ascii=False), encoding="utf-8")
            except OSError as exc:  # a lost record must not lose the work
                logger.warning(f"Could not write the task record: {exc}")
        return task

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def __len__(self) -> int:
        return len(self._tasks)
