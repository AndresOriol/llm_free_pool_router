import time
import logging
from typing import List, Optional

from .base_provider import LLMProvider

logger = logging.getLogger("LLMRouter")


class AutonomousLLMRouter:
    """Selects the best available provider from the pool and tracks cooldowns.

    Selection/cooldown coordination lives here; the actual model invocation and
    failover loop live in the LangChain-facing RouterChatModel, so an agent and
    the smoke test share one code path.
    """

    def __init__(self, providers: List[LLMProvider]):
        self.providers = providers

    def get_best_provider(self) -> Optional[LLMProvider]:
        """Filter to available providers and return the highest priority one."""
        available = [p for p in self.providers if p.check_availability()]

        if not available:
            return None

        available.sort(key=lambda p: p.priority)
        return available[0]

    def seconds_until_available(self) -> Optional[float]:
        """How long until the soonest provider leaves cooldown, or None if some
        provider is already available."""
        waits = [p.cooldown_until - time.time()
                 for p in self.providers if not p.is_available]
        future = [w for w in waits if w > 0]
        return min(future) if future else None

    def generate(self, messages, max_retries: int = 6) -> str:
        """Convenience string generation used by the smoke test.

        Routes through RouterChatModel so there is a single failover path.
        """
        from agent.router_chat_model import RouterChatModel

        model = RouterChatModel(router=self, max_retries=max_retries)
        return model.invoke(messages).content
