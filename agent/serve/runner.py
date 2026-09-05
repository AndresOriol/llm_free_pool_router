"""The pool, built once, and the single worker that spends it.

This is the server's engine. The HTTP layer above it
([app.py](app.py)) does nothing but parse and format; every decision about what
actually runs is here.

Three properties, and each is forced by something already established:

1. **One process, one pool.** Cooldown lives on the provider object and is not
   shared between processes
   ([13.3](../../docs/13-roadmap.md#133-known-constraints-that-shape-the-roadmap)),
   so the pool is built at start-up and every task routes through the *same*
   provider instances. Two servers over one free tier would each rediscover
   which accounts are rate-limited, paying a wasted request each time.

2. **Concurrency is one.** Not a performance choice and not a default to tune:
   the usage ledger is a JSONL file appended from the process, and running two
   sessions at once makes both the ledger and the per-process cooldown
   load-bearing in a way neither is built for
   ([17.6](../../docs/17-deployment.md#176-what-has-to-change-first)). Tasks
   queue. The ceiling is the pool's day anyway -- ~42 minutes of flat-out
   routing across every account
   ([17.4](../../docs/17-deployment.md#174-the-ceiling-is-the-pool-not-the-compute))
   -- so a second worker would not buy throughput, it would buy 429s.

3. **Submission does not block.** A run lasts hours
   ([17.2](../../docs/17-deployment.md#172-what-the-workload-actually-is)) and
   every hosting platform in front of this kills a request in seconds or
   minutes ([17.3](../../docs/17-deployment.md#173-why-requestresponse-platforms-cannot-host-it)).
   So `submit` returns a `submitted` task immediately and the caller polls
   `tasks/get`. This is A2A's own shape -- the spec has a task lifecycle
   precisely because agent work outlives a request -- and it is the reason the
   local transport's blocking `message_send`
   ([protocol/local.py](../protocol/local.py)) is *not* reused here. Same
   objects, same states, same order; different waiting.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
from dataclasses import replace
from pathlib import Path
from typing import Optional

from agent.protocol.local import TaskStore
from agent.protocol.types import AgentCard, Message, Task, TaskState
from agent.serve import workspace

logger = logging.getLogger("harness.serve")

# How many submitted-but-not-started tasks to hold. A bound rather than an
# infinite queue: at one worker and hours per task, a deep backlog is a caller
# that has misunderstood something, and refusing at the door says so while the
# operator can still read the reason.
MAX_QUEUED = 32


class Busy(RuntimeError):
    """The queue is full. The caller should retry later, not louder."""


class Runner:
    """The pool, the queue, the worker, and every task this process has seen."""

    def __init__(self, *, floor: int, recursion_limit: int,
                 allow_shell: bool = False,
                 record_dir: Optional[Path] = None,
                 base_url: str = "") -> None:
        from llm_router import AutonomousLLMRouter, load_providers_from_config
        from agent.code.session import check_floor
        from agent.runtime.chat_model import RouterChatModel

        providers = load_providers_from_config(os.environ.get("ROUTER_CONFIG")
                                               or None)
        if not providers:
            raise SystemExit("No providers loaded. Set your keys in the "
                             "environment (see docs/18-serving.md).")

        self.router = AutonomousLLMRouter(providers)
        # Fail at boot, not on the first request. `check_floor` raises
        # SystemExit when no member is wide enough, and a container that exits
        # at start-up is a deployment that visibly failed -- far better than one
        # that answers /health and rejects every task it is given.
        self.members = check_floor(self.router, floor)
        model = RouterChatModel(router=self.router,
                                max_retries=len(providers) + 3)
        self.model = model.for_context(floor, strict=True)

        self.floor = floor
        self.recursion_limit = recursion_limit
        self.allow_shell = allow_shell
        self.record_dir = Path(record_dir) if record_dir else None
        self.store = TaskStore(self.record_dir)

        self._queue: queue.Queue = queue.Queue(maxsize=MAX_QUEUED)
        self._lock = threading.Lock()
        # What the worker is on right now, by id. Read by `cancel` to tell a
        # queued task (cancellable) from a running one (not).
        self._running: Optional[str] = None
        self._order: list = []  # submission order, for listing

        self.cards = self._build_cards(base_url)

        self._worker = threading.Thread(target=self._work, name="agent-worker",
                                        daemon=True)
        self._worker.start()
        logger.info(f"Serving {', '.join(self.cards) or 'no agents'} on a pool "
                    f"of {len(providers)} provider(s), {self.members} of them "
                    f"at or above the {floor:,}-token floor.")

    # --- discovery ---------------------------------------------------------

    def _build_cards(self, base_url: str) -> dict:
        """The agents this server actually offers, by name.

        Registration is a probe, exactly as it is for local peers
        ([protocol/peers.py](../protocol/peers.py)): `explore` is offered only
        when the search pool can really be reached, because an agent that is
        advertised and then fails costs the caller a whole task to discover it.

        The cards are *copies* with `url` and `preferredTransport` rewritten.
        The originals are module constants shared with the local transport,
        where the honest address is still `local:<name>`, and mutating them
        would make an in-process delegation claim it went over HTTP.
        """
        from agent.code.a2a import CARD as CODE_CARD

        cards = {CODE_CARD.name: self._addressed(CODE_CARD, base_url)}
        self.search_pool = None

        # The imports are in their own `try` because `NoSearchPool` comes from
        # one of them: naming it in an `except` clause that could run before it
        # is bound turns a missing dependency into a NameError raised while
        # handling something else.
        try:
            from llm_router import TavilyPoolRouter
            from agent.explore.a2a import CARD as EXPLORE_CARD
            from agent.explore.session import NoSearchPool, check_pool
        except ImportError as exc:
            logger.warning(f"Not serving the `explore` agent: {exc}")
            return cards

        try:
            pool = TavilyPoolRouter.from_env()
            check_pool(pool)
        # NoSearchPool subclasses SystemExit -- a BaseException -- so `except
        # Exception` does not catch it, and letting it through would take the
        # whole server down over an optional agent.
        except NoSearchPool as exc:
            logger.warning(f"Not serving the `explore` agent: {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Not serving the `explore` agent: {exc!r}")
        else:
            self.search_pool = pool
            cards[EXPLORE_CARD.name] = self._addressed(EXPLORE_CARD, base_url)

        return cards

    @staticmethod
    def _addressed(card: AgentCard, base_url: str) -> AgentCard:
        """The same card with an address on it. A2A's `url` is the agent's own
        endpoint, so each gets its own path rather than the server root."""
        url = f"{base_url.rstrip('/')}/v1/agents/{card.name}" if base_url \
            else f"/v1/agents/{card.name}"
        return replace(card, url=url, preferred_transport="HTTP+JSON")

    # --- the A2A methods ---------------------------------------------------

    def submit(self, agent: str, message: Message, *, workspace_name: str,
               repo: Optional[str] = None, context_id: Optional[str] = None,
               metadata: Optional[dict] = None) -> Task:
        """A2A `message/send`, non-blocking. Returns a `submitted` task.

        The workspace is resolved *here*, on the caller's thread, and not in the
        worker. A bad name or a clone that fails is the caller's error and
        belongs in the response to the request that made it -- discovering it
        an hour later, in a task record nobody is watching, would be the same
        mistake as advertising an agent without probing it.
        """
        if agent not in self.cards:
            known = ", ".join(sorted(self.cards)) or "none"
            raise KeyError(f"no agent named {agent!r} is served here. "
                           f"Served: {known}.")

        # Raises BadWorkspace, which the HTTP layer turns into a 400.
        workdir = workspace.ensure(workspace_name, repo)

        task = Task(metadata={**(metadata or {}), "agent": agent,
                              "workspace": workspace_name,
                              "workdir": str(workdir)})
        if context_id:
            task.context_id = context_id
        message.task_id, message.context_id = task.id, task.context_id
        task.history.append(message)
        task.advance(TaskState.SUBMITTED)

        try:
            self._queue.put_nowait((task.id, agent, workdir))
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
        """A2A `tasks/get`."""
        return self.store.get(task_id)

    def cancel(self, task_id: str) -> Optional[Task]:
        """A2A `tasks/cancel`. Honest about what it can and cannot stop.

        A queued task is cancelled properly: it is marked terminal and the
        worker drops it when it reaches the front. A *running* task is not,
        because stopping one means interrupting a thread inside a provider call
        with a half-written commit and a half-appended ledger line, and a cancel
        that leaves the workspace in a state nobody can describe is worse than
        one that refuses. The refusal is the task, unchanged, in `working` --
        the caller reads the state and knows.
        """
        task = self.store.get(task_id)
        if task is None or task.done:
            return task
        with self._lock:
            if self._running == task_id:
                return task  # still `working`; nothing was cancelled
        task.advance(TaskState.CANCELED, Message.agent(
            "Cancelled before it started.", task_id=task.id,
            context_id=task.context_id))
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

        `keep_awake` is entered here rather than around a single task because
        it is per-thread and lasts as long as the calling thread does
        ([awake.py](../runtime/awake.py)). It is a no-op off Windows, so on a
        Linux container this costs nothing and protects a developer running the
        server on the machine it was written on.
        """
        from agent.runtime.awake import keep_awake

        with keep_awake():
            while True:
                task_id, agent, workdir = self._queue.get()
                try:
                    self._run_one(task_id, agent, workdir)
                except Exception:  # noqa: BLE001
                    # Already handled in _run_one; this is the backstop that
                    # keeps the worker alive. A server that stops running tasks
                    # because one of them raised is the failure this whole
                    # project exists to avoid.
                    logger.exception(f"Worker survived a failure on {task_id}")
                finally:
                    self._queue.task_done()

    def _run_one(self, task_id: str, agent: str, workdir: Path) -> None:
        task = self.store.get(task_id)
        if task is None or task.done:
            logger.info(f"Task {task_id[:8]} was cancelled before it started")
            return

        with self._lock:
            self._running = task_id
        task.advance(TaskState.WORKING)
        self.store.put(task)
        logger.info(f"Task {task.id[:8]} -> {agent} in {workdir}")

        try:
            handler = self._handler(agent, workdir, task)
            task = handler(task)
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"Task {task.id[:8]} raised")
            task.advance(TaskState.FAILED, Message.agent(
                f"The {agent} agent failed: {exc}", task_id=task.id,
                context_id=task.context_id))
        finally:
            with self._lock:
                self._running = None

        if not task.done:  # a handler that forgot to say so
            task.advance(TaskState.COMPLETED, task.status.message)
        self.store.put(task)
        logger.info(f"Task {task.id[:8]} {task.state} "
                    f"({len(task.artifacts)} artifact(s))")

    def _handler(self, agent: str, workdir: Path, task: Task):
        """One handler, bound to this task's workspace.

        Built per task rather than once at start-up, because the workspace is
        per task -- which is the whole point of serving these agents rather than
        running them. Construction is cheap: the handlers close over the model
        and the directory, and the agent graph itself is compiled inside the
        call.
        """
        record_dir = self.record_dir / task.id if self.record_dir else None
        trace_path = (record_dir / "trace.json") if record_dir else None

        if agent == "code":
            from agent.code.a2a import make_handler
            from agent.protocol import peers

            # The served agent delegates through the same local transport the
            # CLI gives it, so a delegated exploration still shares this
            # process's cooldown and lands in this run's trace
            # ([16.3](../../docs/16-agent-protocol.md#163-why-the-transport-is-local)).
            transport = peers.build_transport(
                self.router, self.model, workdir, floor=self.floor,
                members=self.members, recursion_limit=self.recursion_limit,
                record_dir=(record_dir / "a2a") if record_dir else None)
            return make_handler(self.model, workdir, floor=self.floor,
                                members=self.members,
                                recursion_limit=self.recursion_limit,
                                allow_shell=self.allow_shell,
                                transport=transport, trace_path=trace_path)

        if agent == "explore":
            from agent.explore.a2a import make_handler
            return make_handler(self.model, workdir, self.search_pool,
                                floor=self.floor, members=self.members,
                                recursion_limit=self.recursion_limit)

        raise KeyError(f"no handler for agent {agent!r}")
