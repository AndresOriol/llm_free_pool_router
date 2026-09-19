"""The model every agent here runs on: the pool, held to a context floor.

`RouterChatModel` is a `BaseChatModel`, so `create_deep_agent` takes the pool
where a model id would go, with no adapter
([The failover loop](../../docs/pool/failover.md#the-failover-loop)).

**The floor is hard.** Groq's members hold 8,000 input tokens and 100,000 *per
day*; one full-context request would spend an account's entire daily budget. A
conversation cannot be trimmed to fit them, so the model refuses to route below
the floor and waits for a wide member instead. Groq stays in the pool for work
that fits it.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("harness.pool")

# The floor, in input tokens. Sized to admit both Gemini families (250,000) and
# Gemma (128,000) while excluding every Groq member (8,000). A property of the
# pool, not a guess: raising it past 128,000 drops Gemma and leaves only the
# request-scarce Gemini accounts (llm_router/config.yaml).
CONTEXT_FLOOR = 128_000


def check_floor(router, floor: int = CONTEXT_FLOOR) -> int:
    """Fail before the run rather than during it. Returns the eligible count.

    With a hard floor and no member wide enough, every step would walk its retry
    budget and die on "all providers exhausted" -- an error that describes a
    rate limit, not a pool that could never have served this agent.
    """
    wide = [p for p in router.providers
            if p.max_input_tokens is None or p.max_input_tokens >= floor]
    if not wide:
        raise SystemExit(
            f"No provider in the pool holds {floor:,} input tokens, so this "
            f"agent cannot run on it. Widen the floor with AGENT_CONTEXT_FLOOR, "
            f"or add a wide-context member to the pool.")
    logger.info(f"{len(wide)} of {len(router.providers)} providers meet the "
                f"{floor:,}-token floor.")
    return len(wide)


def keyed(model, agent: str):
    """The model carrying `agent`'s harness-profile key, when it can carry one.

    deepagents looks a profile up as `f"{provider}:{identifier}"` and falls back
    to the provider, so this is what lets one agent be configured declaratively
    without configuring all three ([chat_model.py](chat_model.py)). A model that
    is not the pool's -- a fake in a test, or any other `BaseChatModel` -- has no
    profile to key and is returned unchanged.
    """
    for_agent = getattr(model, "for_agent", None)
    return for_agent(agent) if for_agent else model


def connect(floor: int = CONTEXT_FLOOR):
    """(model, eligible member count). Refuses before the run, not during it."""
    from llm_router import AutonomousLLMRouter, load_providers_from_config
    from agent.utils.chat_model import RouterChatModel

    providers = load_providers_from_config(os.environ.get("ROUTER_CONFIG") or None)
    if not providers:
        raise SystemExit("No providers loaded. Set your keys in llm_router/.env.")
    router = AutonomousLLMRouter(providers)
    members = check_floor(router, floor)
    # A step must be able to walk the whole pool once before giving up: with a
    # hard floor the eligible set is smaller than the pool, and an unlucky
    # ordering of benched accounts must not end the run.
    model = RouterChatModel(router=router, max_retries=len(providers) + 3)
    return model.for_context(floor, strict=True), members
