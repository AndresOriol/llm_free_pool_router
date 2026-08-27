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

    def get_best_provider(self, estimated_tokens: Optional[int] = None,
                          min_context: Optional[int] = None,
                          strict_context: bool = False) -> Optional[LLMProvider]:
        """Return the highest-priority available provider that fits the request.

        `estimated_tokens` (from `estimate_tokens`) filters out providers whose
        per-request ceiling the request would overflow, so a large request goes
        straight to a high-capacity model instead of getting rejected (413) by
        every small-TPM Groq account first. A provider with no declared ceiling
        (`max_input_tokens is None`) is never filtered out. When nothing fits,
        fall back to the largest window available -- better to attempt the call
        (and surface the too-large error) than to stall.

        `min_context` is the *caller's* claim about what the job needs, not the
        request's size. Some work cannot be done well on a narrow view even when
        it happens to fit: deciding what to do next, or checking a change against
        a whole codebase, degrade into guessing when the input is trimmed to fit
        a 6,000-token member. Such a caller demands a floor, and the pool honours
        it -- the wide-context members exist for exactly this and should not be
        spent on work that would have run anywhere.

        The floor is a preference by default: if nothing that wide is available
        right now, fall through to the normal rules rather than stall an
        unattended run behind a busy account.

        `strict_context` makes the floor a hard filter instead. A conversational
        harness needs it: falling through to an 8,000-token member is not a
        degraded answer but a failed call, because the history it must carry
        cannot be trimmed to fit without dropping the thing it is reasoning
        about. Returning None here has the caller wait for a wide member to
        leave cooldown, which is the right move when time is free and a narrow
        member could never have served the request
        (docs/design/long-run-harness.md#2-constraints-facts-not-preferences).
        """
        available = [p for p in self.providers if p.check_availability()]
        if not available:
            return None

        if min_context:
            wide = [p for p in available
                    if p.max_input_tokens is None or p.max_input_tokens >= min_context]
            if wide:
                available = wide
            elif strict_context:
                return None

        if estimated_tokens is None:
            return min(available, key=lambda p: p.priority)

        fits = [p for p in available
                if p.max_input_tokens is None
                or p.max_input_tokens * _FIT_SAFETY >= estimated_tokens]
        if fits:
            return min(fits, key=lambda p: p.priority)
        return max(available, key=lambda p: p.max_input_tokens or 0)

    def seconds_until_available(self,
                                min_context: Optional[int] = None) -> Optional[float]:
        """How long until the soonest provider leaves cooldown, or None if some
        provider is already available.

        A retired member is skipped: it is unavailable forever, and counting it
        here would have the caller sleep waiting for a model that no longer
        exists.

        `min_context` narrows the question to members that could actually serve
        this caller, and must be passed by anyone selecting with
        `strict_context`. Without it the two disagree: selection refuses a
        narrow member while this reports "someone is already available" because
        that same narrow member is warm, so the caller stops waiting and the run
        dies with a pool that was about to free up.
        """
        candidates = [p for p in self.providers
                      if not p.is_available and not p.decommissioned]
        if min_context:
            candidates = [p for p in candidates
                          if p.max_input_tokens is None
                          or p.max_input_tokens >= min_context]
        waits = [p.cooldown_until - time.time() for p in candidates]
        future = [w for w in waits if w > 0]
        return min(future) if future else None
