"""The queue, and the single worker that runs one agent command at a time.

This is the server's engine. The HTTP layer above it ([app.py](app.py)) does
nothing but parse and format; every decision about what actually runs is here.

Three properties, and each is forced by something already established:

1. **A task is a command.** The worker runs `python -m agent.<name> <workdir>
   --task "..."` and keeps what it printed. There is no in-process handler and
   no protocol object, because an agent is already a command and a caller that
   is not a person at a terminal needs a queue and an id, not a vocabulary
   ([16. Delegation](../../docs/16-delegation.md)).

2. **Concurrency is one.** Not a performance choice and not a default to tune:
   the usage ledger is a JSONL file appended from the process, and cooldown
   lives on the provider object and is not shared between processes
   ([13.3](../../docs/13-roadmap.md#133-known-constraints-that-shape-the-roadmap)).
   Two agent processes at once make both load-bearing in a way neither is built
   for ([17.6](../../docs/17-deployment.md#176-what-has-to-change-first)). Tasks
   queue. The ceiling is the pool's day anyway -- ~42 minutes of flat-out
   routing across every account
   ([17.4](../../docs/17-deployment.md#174-the-ceiling-is-the-pool-not-the-compute))
   -- so a second worker would not buy throughput, it would buy 429s.

3. **Submission does not block.** A run lasts hours
   ([17.2](../../docs/17-deployment.md#172-what-the-workload-actually-is)) and
   every hosting platform in front of this kills a request in seconds or
   minutes ([17.3](../../docs/17-deployment.md#173-why-requestresponse-platforms-cannot-host-it)).
   So `submit` returns a queued task immediately and the caller polls.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
from pathlib import Path
from typing import Optional

from agent import delegation
from agent.serve import workspace
from agent.serve.task import (CANCELED, DONE, FAILED, RUNNING, Task, TaskStore)

logger = logging.getLogger("harness.serve")

# How many submitted-but-not-started tasks to hold. A bound rather than an
# infinite queue: at one worker and hours per task, a deep backlog is a caller
# that has misunderstood something, and refusing at the door says so while the
# operator can still read the reason.
MAX_QUEUED = 32


class Busy(RuntimeError):
    """The queue is full. The caller should retry later, not louder."""


class Runner:
    """The queue, the worker, and every task this process has seen."""

    def __init__(self, *, floor: int, allow_shell: bool = False,
                 record_dir: Optional[Path] = None) -> None:
        self.floor = floor
        self.allow_shell = allow_shell
        self.record_dir = Path(record_dir) if record_dir else None
        self.store = TaskStore(self.record_dir)

        # Fail at boot, not on the first request. A container that exits at
        # start-up is a deployment that visibly failed -- far better than one
        # that answers /health and rejects every task it is given. The pool is
        # then discarded: each task builds its own in its own process.
        self.providers, self.members = _check_pool(floor)

        # Which agents this server offers, probed rather than declared, exactly
        # as delegation does it ([agent/delegation.py](../delegation.py)): an
        # agent advertised and then unable to work costs a caller a whole task
        # to discover it.
        self.agents = delegation.available(("code", "explore", "improve"))

        self._queue: queue.Queue = queue.Queue(maxsize=MAX_QUEUED)
        self._lock = threading.Lock()
        # What the worker is on right now, by id. Read by `cancel` to tell a
        # queued task (cancellable) from a running one (not).
        self._running: Optional[str] = None
        self._order: list = []  # submission order, for listing

        self._worker = threading.Thread(target=self._work, name="agent-worker",
                                        daemon=True)
        self._worker.start()
        logger.info(f"Serving {', '.join(self.agents) or 'no agents'} on a pool "
                    f"of {self.providers} provider(s), {self.members} of them "
                    f"at or above the {floor:,}-token floor.")

    # --- submission --------------------------------------------------------

    def submit(self, agent: str, request: str, *, workspace_name: str,
               repo: Optional[str] = None,
               metadata: Optional[dict] = None) -> Task:
        """Queue one run. Returns it immediately, in `queued`.

        The workspace is resolved *here*, on the caller's thread, and not in the
        worker. A bad name or a clone that fails is the caller's error and
        belongs in the response to the request that made it -- discovering it an
        hour later, in a task record nobody is watching, would be the same
        mistake as advertising an agent without probing it.
        """
        if agent not in self.agents:
            known = ", ".join(sorted(self.agents)) or "none"
            raise KeyError(f"no agent named {agent!r} is served here. "
                           f"Served: {known}.")

        # Raises BadWorkspace, which the HTTP layer turns into a 400.
        workdir = workspace.ensure(workspace_name, repo)

        task = Task(agent=agent, request=request, workspace=workspace_name,
                    workdir=str(workdir), metadata=dict(metadata or {}))
        try:
            self._queue.put_nowait(task.id)
        except queue.Full:
            raise Busy(f"{MAX_QUEUED} tasks are already queued; this server "
                       f"runs one at a time.")

        with self._lock:
            self._order.append(task.id)
        self.store.put(task)
        logger.info(f"Task {task.id[:8]} queued for {agent} "
                    f"in workspace {workspace_name!r}")
        return task

    def get(self, task_id: str) -> Optional[Task]:
        return self.store.get(task_id)

    def cancel(self, task_id: str) -> Optional[Task]:
        """Honest about what it can and cannot stop.

        A queued task is cancelled properly: it is marked terminal and the
        worker drops it when it reaches the front. A *running* task is not,
        because stopping one means killing an agent process with a half-written
        commit and a half-appended ledger, and a cancel that leaves the
        workspace in a state nobody can describe is worse than one that refuses.
        The refusal is the task, unchanged, in `running` -- the caller reads the
        state and knows.
        """
        task = self.store.get(task_id)
        if task is None or task.done:
            return task
        with self._lock:
            if self._running == task_id:
                return task  # still `running`; nothing was cancelled
        task.advance(CANCELED, error="Cancelled before it started.")
        return self.store.put(task)

    def recent(self, limit: int = 50) -> list:
        """The most recent tasks, newest first. For an operator, not an agent."""
        with self._lock:
            ids = list(reversed(self._order[-limit:]))
        return [t for t in (self.store.get(i) for i in ids) if t is not None]

    @property
    def queued(self) -> int:
        return self._queue.qsize()

    @property
    def running(self) -> Optional[str]:
        with self._lock:
            return self._running

    # --- the worker --------------------------------------------------------

    def _work(self) -> None:
        """The one thread that runs agents. Never exits, never raises out.

        `keep_awake` is entered here rather than around a single task because it
        is per-thread and lasts as long as the calling thread does
        ([awake.py](../runtime/awake.py)). It is a no-op off Windows, so on a
        Linux container this costs nothing and protects a developer running the
        server on the machine it was written on.
        """
        from agent.runtime.awake import keep_awake

        with keep_awake():
            while True:
                task_id = self._queue.get()
                try:
                    self._run_one(task_id)
                except Exception:  # noqa: BLE001
                    # Already handled in _run_one; this is the backstop that
                    # keeps the worker alive. A server that stops running tasks
                    # because one of them raised is the failure this whole
                    # project exists to avoid.
                    logger.exception(f"Worker survived a failure on {task_id}")
                finally:
                    self._queue.task_done()

    def _run_one(self, task_id: str) -> None:
        task = self.store.get(task_id)
        if task is None or task.done:
            logger.info(f"Task {task_id[:8]} was cancelled before it started")
            return

        with self._lock:
            self._running = task_id
        task.advance(RUNNING)
        self.store.put(task)
        logger.info(f"Task {task.id[:8]} -> {task.agent} in {task.workdir}")

        try:
            code, output = delegation.run(task.agent, task.workdir,
                                          task.request)
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"Task {task.id[:8]} raised")
            code, output = 1, f"The {task.agent} agent could not be run: {exc}"
        finally:
            with self._lock:
                self._running = None

        if code == 0:
            task.advance(DONE, output=output, exit_code=code)
        elif code is None:
            # A timeout is not a crash. The session did real work and was
            # stopped; whatever it committed is still committed.
            task.advance(FAILED, output=output,
                         error="The session hit the delegation timeout and was "
                               "stopped. What it had committed is committed.")
        else:
            task.advance(FAILED, output=output, exit_code=code,
                         error=f"`python -m agent.{task.agent}` exited {code}.")
        self.store.put(task)
        logger.info(f"Task {task.id[:8]} {task.state}")


def _check_pool(floor: int) -> tuple:
    """(providers, members wide enough). Raises SystemExit if it cannot serve."""
    from llm_router import AutonomousLLMRouter, load_providers_from_config
    from agent.runtime.pool import check_floor

    providers = load_providers_from_config(os.environ.get("ROUTER_CONFIG")
                                           or None)
    if not providers:
        raise SystemExit("No providers loaded. Set your keys in the "
                         "environment (see docs/18-serving.md).")
    return len(providers), check_floor(AutonomousLLMRouter(providers), floor)
