"""The one question the router can ask before spending a request: has this
member any requests-per-day left?

Everything else in this package renders the ledger for a human, and
[14.9](../../docs/14-quota-panel.md#149-what-it-deliberately-doesnt-do) argued
that none of it should touch routing. That argument is now half-kept. The report
still never gates a call; this module answers one boolean off the same
arithmetic, and the router uses it to *skip* a member rather than to *choose*
one -- see [4.2](../../docs/04-failover.md#42-size-aware-selection).

Only requests-per-day. A daily ceiling is the one budget a cooldown cannot
represent: a rate limit hands back a `Retry-After` measured in seconds, so the
member returns to the pool and is asked again, and on Gemini's twenty requests
a day that costs a refusal every time round the loop for the rest of the day.
The per-minute windows need none of this -- they clear on their own, which is
what a cooldown already is.

**Wrong in the tolerable direction, by construction.** The count is this
router's own ([14.4](../../docs/14-quota-panel.md#144-one-source-and-what-it-misses)),
the window model is approximate ([14.5](../../docs/14-quota-panel.md#145-windows-and-when-they-reset)),
and a reading is reused for `ttl` seconds, so a burst can spend past a ceiling
this still calls open. Every one of those errors ends in an attempt the provider
refuses, which is exactly the path that already worked. The opposite error --
refusing to route to a member the vendor would have served -- is the one that
stalls an unattended run, so nothing here may ever be the last word: an
unreadable ledger, a missing snapshot, an undeclared limit and an exception all
mean *available*, and the router keeps its own fallback for a pool this says is
entirely spent.
"""

import logging
import os
import time
from pathlib import Path
from typing import Optional, Set

from .. import usage
from .ledger import read_ledger, read_pool
from .report import build_report

logger = logging.getLogger("LLMRouter")

#: How long one reading of the ledger is reused. The router asks once per
#: attempt and the ledger only grows, so re-parsing it every time would put a
#: file read in front of every model call for a number that moves slowly: a
#: daily budget measured over 24 hours. Overspend inside the window is bounded
#: by what the pool can issue in `ttl` seconds, and lands on the retry path.
DEFAULT_TTL_SECONDS = 30.0

#: Set to "0" to route exactly as the pool did before this filter existed --
#: the off switch for an A/B (docs/08-evaluation-method.md).
ENV_VAR = "LLM_ROUTER_RPD_FILTER"


def exhausted_rpd(directory=None, now: Optional[float] = None) -> Set[str]:
    """The names of pool members whose requests-per-day ceiling is spent.

    Read straight off the report, so this and the panel can never disagree: the
    same gauge a human reads as `18/20 90%` is the one tested here. A member is
    named only when it declares an `rpd` limit in `config.yaml` and the ledger's
    24-hour window already holds that many requests -- refusals included, since
    the provider answered them and they spent budget
    ([14.6](../../docs/14-quota-panel.md#146-how-a-refused-attempt-is-counted)).

    Raises whatever the ledger raises. `RpdBudget` is what the router holds, and
    it is the layer that turns a failure into "available".
    """
    directory = Path(directory) if directory else usage.usage_dir()
    report = build_report(read_ledger(directory), read_pool(directory), now)
    return {row.provider for row in report.rows
            for gauge in row.gauges
            if gauge.name == "rpd" and gauge.limit and gauge.used >= gauge.limit}


class RpdBudget:
    """The router's view of the ledger: who is out of requests for today.

    Holds the last reading and its age. Constructed once per router, so the
    cache is per process and a fresh run starts by reading what the day has
    already spent -- which is the whole reason this comes off the ledger rather
    than an in-memory counter. A daily budget outlives the process that spends
    it.
    """

    def __init__(self, directory=None, ttl: float = DEFAULT_TTL_SECONDS,
                 enabled: Optional[bool] = None):
        self.directory = directory
        self.ttl = ttl
        self.enabled = (os.environ.get(ENV_VAR, "1") != "0" if enabled is None
                        else enabled)
        self._exhausted: Set[str] = set()
        self._read_at: Optional[float] = None

    def exhausted(self) -> Set[str]:
        """The current reading, refreshed at most every `ttl` seconds."""
        if not self.enabled:
            return set()

        now = time.time()
        if self._read_at is not None and now - self._read_at < self.ttl:
            return self._exhausted

        try:
            self._exhausted = exhausted_rpd(self.directory, now)
        except Exception as exc:  # noqa: BLE001 - see the module docstring
            logger.debug(f"Could not read the usage ledger for RPD budget: {exc!r}")
            self._exhausted = set()
        self._read_at = now
        return self._exhausted

    def has_budget(self, provider) -> bool:
        """Is this member worth asking? True whenever we cannot say otherwise."""
        return getattr(provider, "name", None) not in self.exhausted()
