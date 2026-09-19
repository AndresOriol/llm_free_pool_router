import time
import logging
import math
from typing import List, Optional

from .base_provider import LLMProvider
from .quota.budget import RpdBudget
from .quota.windows import resets_in

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

    def __init__(self, providers: List[LLMProvider], quota: Optional[RpdBudget] = None):
        """`quota` answers which members have spent their requests-per-day.

        Defaults to reading this router's own usage ledger
        ([quota/budget.py](quota/budget.py)). Pass one built over another
        directory to point it elsewhere, or one constructed with
        `enabled=False` to route exactly as the pool did before the filter
        existed.
        """
        self.providers = providers
        self.quota = RpdBudget() if quota is None else quota

    def get_best_provider(self, estimated_tokens: Optional[int] = None,
                          min_context: Optional[int] = None,
                          strict_context: bool = False,
                          attempted: Optional[set[str]] = None) -> Optional[LLMProvider]:
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

        Finally, a member the usage ledger says has spent its requests-per-day
        is passed over silently, in `_cheapest`. That is the one exhaustion a
        cooldown cannot express: the vendor's refusal carries a `Retry-After`
        measured in seconds, so the member returns to the pool and is asked
        again, and against a twenty-a-day ceiling that is a fresh refusal every
        time round the loop until midnight. Skipping it is the difference
        between paying for that discovery once and paying for it all day.

        **It is the last word on nothing.** The count is our own, over a window
        model we know is approximate
        (docs/pool/quota.md#windows-and-when-they-reset), so it may not
        overrule anything factual: it picks *among* the members that fit the
        request and clear the floor, never across them -- a member that cannot
        hold the job is not made preferable by having budget left. And if none
        of the candidates has budget, they are all offered anyway and the call
        is attempted. Being wrong that way costs one refusal on a retry path
        that already handles it; being wrong the other way stalls a run the
        provider would have served.
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
            return self._cheapest(available, attempted)

        fits = [p for p in available
                if p.max_input_tokens is None
                or p.max_input_tokens * _FIT_SAFETY >= estimated_tokens]
        if fits:
            tpm_fits = getattr(self.quota, "tpm_fits", lambda _p, _n: True)
            within_tpm = [p for p in fits if tpm_fits(p, estimated_tokens)]
            if within_tpm:
                return self._cheapest(within_tpm, attempted)
            # Every otherwise-valid member is locally known to overflow its
            # minute token bucket. Put them to sleep until that fixed window
            # rolls so the caller waits instead of deliberately buying 429s.
            for provider in fits:
                provider.trigger_cooldown(math.ceil(
                    resets_in("tpm", getattr(provider, "platform", ""), time.time())))
            return None
        # Nothing fits, so the largest window is the only thing that might: it
        # is offered whatever the ledger says about its budget, because a member
        # too small for the request is not an alternative to one that is spent.
        return max(available, key=lambda p: p.max_input_tokens or 0)

    def _cheapest(self, candidates: List[LLMProvider],
                  attempted: Optional[set[str]] = None) -> LLMProvider:
        """The highest-priority candidate, preferring ones with quota left.

        `funded or candidates` is the whole tolerance rule: when the ledger says
        every candidate is spent it says nothing useful, so priority decides as
        it always did and the provider gets the last word.
        """
        # The ledger's funded set remains the first preference. Within it, slow
        # failures can outlast another member's cooldown, so try the rest of
        # the fitting pool before revisiting those failures in this request.
        # This is a preference: a lone recovered member must still be usable.
        funded = [p for p in candidates if self.quota.has_budget(p)]
        candidates = funded or candidates
        untried = [p for p in candidates if p.name not in (attempted or ())]
        return min(untried or candidates, key=lambda p: p.priority)

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
