import time
import logging
from typing import List, Optional

from .base_provider import LLMProvider

logger = logging.getLogger("LLMRouter")

# Leave headroom below a model's declared ceiling: the token estimate is a rough
# chars/4 heuristic and a model's output shares the same TPM budget on Groq.
_FIT_SAFETY = 0.9


class AutonomousLLMRouter:
    """Selects the best available provider from the pool and tracks cooldowns.

    Selection/cooldown coordination lives here; the actual model invocation and
    failover loop live in the LangChain-facing RouterChatModel, so an agent and
    the smoke test share one code path (both wrap a router in a RouterChatModel).
    """

    def __init__(self, providers: List[LLMProvider]):
        self.providers = providers

    def get_best_provider(self, estimated_tokens: Optional[int] = None) -> Optional[LLMProvider]:
        """Return the highest-priority available provider that fits the request.

        `estimated_tokens` (from `estimate_tokens`) filters out providers whose
        per-request ceiling the request would overflow, so a large request goes
        straight to a high-capacity model instead of getting rejected (413) by
        every small-TPM Groq account first. A provider with no declared ceiling
        (`max_input_tokens is None`) is never filtered out. When nothing fits,
        fall back to the largest window available -- better to attempt the call
        (and surface the too-large error) than to stall.
        """
        available = [p for p in self.providers if p.check_availability()]
        if not available:
            return None
        if estimated_tokens is None:
            return min(available, key=lambda p: p.priority)

        fits = [p for p in available
                if p.max_input_tokens is None
                or p.max_input_tokens * _FIT_SAFETY >= estimated_tokens]
        if fits:
            return min(fits, key=lambda p: p.priority)
        return max(available, key=lambda p: p.max_input_tokens or 0)

    def seconds_until_available(self) -> Optional[float]:
        """How long until the soonest provider leaves cooldown, or None if some
        provider is already available."""
        waits = [p.cooldown_until - time.time()
                 for p in self.providers if not p.is_available]
        future = [w for w in waits if w > 0]
        return min(future) if future else None
