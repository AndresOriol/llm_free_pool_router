"""Who is reachable, and what each of them says it can do.

A2A discovery is an HTTP fetch of `/.well-known/agent-card.json` from a URL you
already knew. With one process there is no URL and no fetch, so discovery
collapses into a dict: name -> (card, handler). The *card* is unchanged, which is
the part that matters -- what a caller reasons about is the card either way, and
swapping this for a fetch does not change a line of the calling agent.

**Registration is a capability probe, not a declaration.** The explorer is
registered only when the pool can actually search the web, on the same principle
that decides which member serves a search: a vendor's capability grant is a fact
to probe, not to infer
([15.2.1](../../docs/15-explorer.md#1521-a-capability-is-a-fact-to-probe-not-to-infer)).
An agent listed but unreachable is worse than one absent -- the caller spends a
delegation, and its budget, discovering the gap.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from agent.protocol.types import AgentCard, Task

logger = logging.getLogger("harness.protocol")

# What a registered agent is, on the local transport: something that takes a
# Task carrying the request in `history[-1]` and returns it in a terminal state.
# Deliberately the same shape a server-side A2A `AgentExecutor` has, so an HTTP
# binding wraps a handler rather than replacing it.
Handler = Callable[[Task], Task]


class AgentRegistry:
    """The peers one agent can address. Empty is a normal, expected state."""

    def __init__(self) -> None:
        self._agents: dict[str, tuple[AgentCard, Handler]] = {}

    def register(self, card: AgentCard, handler: Handler) -> None:
        if card.name in self._agents:
            raise ValueError(f"agent {card.name!r} is already registered")
        self._agents[card.name] = (card, handler)
        logger.info(f"Registered peer agent {card.name!r} "
                    f"({len(card.skills)} skill(s), {card.preferred_transport})")

    def card(self, name: str) -> Optional[AgentCard]:
        found = self._agents.get(name)
        return found[0] if found else None

    def handler(self, name: str) -> Optional[Handler]:
        found = self._agents.get(name)
        return found[1] if found else None

    @property
    def names(self) -> list:
        return sorted(self._agents)

    def cards(self) -> list:
        return [card for card, _ in self._agents.values()]

    def __bool__(self) -> bool:
        return bool(self._agents)

    def __len__(self) -> int:
        return len(self._agents)


def directory_section(registry: AgentRegistry) -> str:
    """The cards as a prompt section, or '' when nothing is reachable.

    **Discovery is prose in the system prompt, not a `list_agents` tool**, and
    that is a measured choice rather than a stylistic one: tool schemas are 91%
    of what a step spends
    ([6.4](../../docs/06-agent.md#64-why-it-is-shaped-this-way)), so a second
    tool would be charged on *every* step of *every* run to describe
    a directory that changes once at startup. In the prompt it is charged once
    and is already there when the agent decides whether to delegate at all.
    """
    if not registry:
        return ""

    lines = ["### Other agents you can delegate to", "",
             "Use the `delegate` tool. Each call runs a full agent session and "
             "spends the same free-tier pool your own turns do, so ask once, "
             "ask specifically, and read the files it leaves behind rather than "
             "asking again.", ""]
    for card in registry.cards():
        lines.append(f"**{card.name}** — {card.description}")
        for skill in card.skills:
            lines.append(f"- `{skill.id}`: {skill.description}")
            for example in skill.examples[:2]:
                lines.append(f"  - e.g. {example}")
        lines.append("")
    return "\n".join(lines).rstrip()
