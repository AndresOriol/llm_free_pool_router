"""The local transport binding: A2A's methods with the network taken out.

A2A defines its methods over JSON-RPC 2.0 (`message/send`, `tasks/get`,
`tasks/cancel`) and is explicit that the transport is a binding, not the
protocol. This is a binding whose wire is a Python call. The two methods below
carry the same objects, in the same states, in the same order as the HTTP
binding would; what is missing is the socket.

**Why local rather than a server per agent, on this project specifically.**

1. **Cooldown lives on the provider object, in this process.** Two agents in two
   processes each rediscover which accounts are rate-limited, and pay a wasted
   request each to find out
   ([13.3](../../docs/13-roadmap.md#133-known-constraints-that-shape-the-roadmap)).
   In-process, the delegate routes through the *same* provider instances as its
   caller, so a delegated search that exhausts an account is immediately visible
   to the conversation that asked for it. Two routers over one free tier is the
   honest model, and it only works inside one process
   ([15.4](../../docs/15-explorer.md#154-which-member-serves-a-search)).
2. **The delegate's calls land in the caller's trace.** One run, one record, one
   `input_tokens` total -- which is the number the whole evaluation rests on
   ([10. Metrics](../../docs/10-metrics.md)). Split across processes, a
   delegation costs quota that no run's record accounts for.
3. **A server per agent is a port, a process supervisor and a dependency**
   bought to connect two callers that are already in the same interpreter.

None of that is an argument against HTTP later; it is an argument for paying for
it when a peer is genuinely remote. The schema is what makes that cheap, and the
schema is already the standard's.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from agent.protocol.registry import AgentRegistry
from agent.protocol.types import Message, Task, TaskState

logger = logging.getLogger("harness.protocol")


class TaskStore:
    """Every task this process has seen, and optionally a copy on disk.

    In memory because `tasks/get` has to answer for a task the caller already
    holds an id for. On disk because a delegation spends real free-tier quota
    unattended, and the account a human reads afterwards should say what was
    asked and what came back without depending on LangSmith still holding the
    trace ([7.6](../../docs/07-observability.md#76-the-record-one-run-tree)).

    The directory is the *run record's*, never the workdir: the coding agent
    commits its workdir, and a protocol log committed into the project under
    review is noise in every diff it produces afterwards.
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


class LocalTransport:
    """`message/send` and `tasks/get`, dispatched through the registry."""

    def __init__(self, registry: AgentRegistry,
                 store: Optional[TaskStore] = None) -> None:
        self.registry = registry
        # `is None`, not `or`: a TaskStore has a __len__, so an empty one is
        # falsy, and `store or TaskStore()` silently threw away the caller's
        # store -- along with the directory it was going to write records to.
        self.store = TaskStore() if store is None else store

    def message_send(self, agent: str, message: Message,
                     context_id: Optional[str] = None,
                     metadata: Optional[dict] = None) -> Task:
        """A2A `message/send`. Blocks until the task reaches a terminal state.

        The spec allows a server to answer either with a `Message` (for trivial
        exchanges) or a `Task`. This always answers with a Task: a delegation
        here costs a whole agent session, so it always deserves a record, and a
        caller that only ever handles one shape has one code path instead of two.

        An unknown agent is `REJECTED` rather than an exception. The caller is a
        language model reading a tool result; a refusal it can read and correct
        beats a traceback that ends the run.
        """
        task = Task(metadata=dict(metadata or {}))
        if context_id:  # continuing a conversation rather than starting one
            task.context_id = context_id
        message.task_id, message.context_id = task.id, task.context_id
        task.history.append(message)

        handler = self.registry.handler(agent)
        if handler is None:
            known = ", ".join(self.registry.names) or "none"
            task.advance(TaskState.REJECTED, Message.agent(
                f"No agent named {agent!r} is reachable. Reachable: {known}.",
                task_id=task.id, context_id=task.context_id))
            return self.store.put(task)

        task.advance(TaskState.WORKING)
        self.store.put(task)
        logger.info(f"Task {task.id[:8]} -> {agent}: {message.text[:120]!r}")

        try:
            task = handler(task)
        except Exception as exc:  # noqa: BLE001
            # The delegate failing is not the caller failing. It gets a FAILED
            # task and decides what to do; ending the parent run over a
            # subordinate's crash would throw away work that is already done.
            logger.exception(f"Task {task.id[:8]} raised")
            task.advance(TaskState.FAILED, Message.agent(
                f"The {agent} agent failed: {exc}", task_id=task.id,
                context_id=task.context_id))

        if not task.done:  # a handler that forgot to say so
            task.advance(TaskState.COMPLETED, task.status.message)
        logger.info(f"Task {task.id[:8]} {task.state} "
                    f"({len(task.artifacts)} artifact(s))")
        return self.store.put(task)

    def tasks_get(self, task_id: str) -> Optional[Task]:
        """A2A `tasks/get`. Present so a caller can re-read a task by id."""
        return self.store.get(task_id)


def render(task: Task, agent: str) -> str:
    """One task as the text a calling model reads back from its tool.

    The artifacts are listed as *paths*, not contents. A research note runs to
    thousands of tokens and the caller may want one line of it; making it read
    the file with the tool it already has keeps the choice, and the cost, where
    the caller can see them.
    """
    lines = [f"task {task.id} to `{agent}` — **{task.state}**", ""]

    closing = task.status.message.text if task.status.message else ""
    if closing:
        lines += [closing, ""]

    if task.artifacts:
        lines.append("Artifacts (read these files for the detail):")
        for artifact in task.artifacts:
            for part in artifact.parts:
                uri = getattr(part, "uri", None)
                if uri:
                    size = artifact.metadata.get("bytes")
                    lines.append(f"- `{uri}`" + (f" ({size:,} bytes)" if size
                                                 else ""))
        lines.append("")
    elif task.state == TaskState.COMPLETED:
        # The loudest thing this can say: the delegate ran, spent quota and left
        # nothing behind. Silence here reads as success and is not.
        lines += ["It produced no files. Nothing was written down, so nothing "
                  "survives this message — treat the answer above as unsourced.",
                  ""]

    return "\n".join(lines).rstrip()
