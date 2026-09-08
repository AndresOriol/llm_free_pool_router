"""Composition: which agents are reachable from this process, and on what pool.

The only module that knows about all three agents at once. `agent/code`,
`agent/explore` and `agent/improve` stay ignorant of each other -- an agent
knows it has a delegation tool and a directory of cards, and nothing about what
is behind them, which is the property that let the third one be added by editing
this file and no other.

**Who may call whom is a caller's decision, not a global graph.** The coding
agent is offered `explore` and never `code`; the improvement agent is offered
`code` and never itself. Nothing here prevents a cycle in general -- what
prevents this one is that the peer set is passed in, and a `code` peer is built
with the *coding agent's* default peers rather than the caller's.

Registration is a probe. `explore` is offered only if the pool can actually
reach the web, and the check is the explorer's own `check_pool`, not a guess
from the config file: an agent that is advertised and then fails costs the caller
a delegation to discover it
([registry](registry.py)).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional, Sequence

from agent.protocol.local import LocalTransport, TaskStore
from agent.protocol.registry import AgentRegistry

logger = logging.getLogger("harness.protocol")

# Which peers to offer. Default on, so this branch is a configuration that can
# be measured against a baseline rather than a feature nobody exercises
# ([13.7](../../docs/13-roadmap.md#137-how-to-propose-a-change)). Set
# AGENT_PEERS= (empty) to run the coding agent alone.
_ENV_VAR = "AGENT_PEERS"
DEFAULT_PEERS = ("explore",)


def requested_peers() -> tuple:
    raw = os.environ.get(_ENV_VAR)
    if raw is None:
        return DEFAULT_PEERS
    return tuple(name.strip() for name in raw.split(",") if name.strip())


KNOWN_PEERS = ("explore", "code")


def build_transport(router, model, workdir: Path, *, floor: int, members: int,
                    recursion_limit: int,
                    record_dir: Optional[Path] = None,
                    peers: Optional[Sequence[str]] = None,
                    allow_shell: bool = False) -> Optional[LocalTransport]:
    """The transport an agent delegates through, or None.

    `peers` names who to offer; left unset it is the coding agent's default,
    read from `AGENT_PEERS`. The improvement agent passes `("code",)` because
    what it delegates is a fix, and being able to ask for research instead would
    just be a second way to spend the day
    ([19](../../docs/19-improvement-agent.md)).

    None rather than an empty transport, so the caller has one thing to test and
    the tool is either present and usable or absent -- never present and certain
    to refuse.
    """
    wanted = tuple(peers) if peers is not None else requested_peers()
    if not wanted:
        logger.info("Peer agents disabled; this agent runs alone.")
        return None

    registry = AgentRegistry()

    if "explore" in wanted:
        _register_explore(registry, router, model, workdir, floor=floor,
                          members=members, recursion_limit=recursion_limit)
    if "code" in wanted:
        _register_code(registry, router, model, workdir, floor=floor,
                       members=members, recursion_limit=recursion_limit,
                       record_dir=record_dir, allow_shell=allow_shell)

    unknown = [n for n in wanted if n not in KNOWN_PEERS]
    if unknown:
        logger.warning(f"Unknown peer(s): {', '.join(unknown)}")

    if not registry:
        return None
    return LocalTransport(registry, TaskStore(record_dir))


def _register_code(registry: AgentRegistry, router, model, workdir: Path, *,
                   floor: int, members: int, recursion_limit: int,
                   record_dir: Optional[Path], allow_shell: bool) -> None:
    """Offer the coding agent, with the peers *it* would have had.

    Its own transport is built from the coding agent's own peer setting with
    `code` filtered out, which is what keeps the graph acyclic without a cycle
    check: a delegated coding session can reach `explore` and cannot reach
    `code`, so a delegation cannot come back round to the agent that made it.
    Filtering rather than hardcoding `DEFAULT_PEERS` is what keeps `AGENT_PEERS=`
    meaning what it says -- an operator who turned delegation off must not get
    it back through a coding session someone else delegated.

    There is no probe here, unlike the explorer's. The coding agent needs
    nothing beyond the pool the caller is already running on, so an agent that
    is advertised is an agent that works.
    """
    from agent.code.a2a import CARD, make_handler

    inner = build_transport(router, model, workdir, floor=floor,
                            members=members, recursion_limit=recursion_limit,
                            record_dir=record_dir, allow_shell=allow_shell,
                            peers=tuple(p for p in requested_peers()
                                        if p != "code"))
    registry.register(CARD, make_handler(
        model, workdir, floor=floor, members=members,
        recursion_limit=recursion_limit, allow_shell=allow_shell,
        transport=inner))


def _register_explore(registry: AgentRegistry, router, model, workdir: Path, *,
                      floor: int, members: int, recursion_limit: int) -> None:
    from llm_router import TavilyPoolRouter

    from agent.explore.a2a import CARD, make_handler
    from agent.explore.session import NoSearchPool, check_pool

    # The probe is now "is there a Tavily account", not "can a pool member
    # ground a call": search moved to Tavily so that a search returns the page
    # rather than a summary of it (docs/15-explorer.md#157).
    pool = TavilyPoolRouter.from_env()
    try:
        check_pool(pool)
    except NoSearchPool as exc:
        # Not fatal. The coding agent is perfectly able to work without a
        # researcher; it just must not be told it has one.
        logger.warning(f"Not offering the `explore` agent: {exc}")
        return

    registry.register(CARD, make_handler(model, workdir, pool, floor=floor,
                                         members=members,
                                         recursion_limit=recursion_limit))
