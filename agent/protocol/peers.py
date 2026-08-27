"""Composition: which agents are reachable from this process, and on what pool.

The only module that knows about both agents at once. `agent/code` and
`agent/explore` stay ignorant of each other -- the coding agent knows it has a
`delegate` tool and a directory of cards, and nothing about what is behind them,
which is the property that lets a third agent be added by editing this file and
no other.

Registration is a probe. `explore` is offered only if the pool can actually
reach the web, and the check is the explorer's own `check_search`, not a guess
from the config file: an agent that is advertised and then fails costs the caller
a delegation to discover it
([registry](registry.py)).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

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


def build_transport(router, model, workdir: Path, *, floor: int, members: int,
                    recursion_limit: int,
                    record_dir: Optional[Path] = None) -> Optional[LocalTransport]:
    """The transport the coding agent delegates through, or None.

    None rather than an empty transport, so the caller has one thing to test and
    the tool is either present and usable or absent -- never present and certain
    to refuse.
    """
    wanted = requested_peers()
    if not wanted:
        logger.info("Peer agents disabled; the coding agent runs alone.")
        return None

    registry = AgentRegistry()

    if "explore" in wanted:
        _register_explore(registry, router, model, workdir, floor=floor,
                          members=members, recursion_limit=recursion_limit)

    unknown = [n for n in wanted if n not in {"explore"}]
    if unknown:
        logger.warning(f"Unknown peer(s) in {_ENV_VAR}: {', '.join(unknown)}")

    if not registry:
        return None
    return LocalTransport(registry, TaskStore(record_dir))


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
