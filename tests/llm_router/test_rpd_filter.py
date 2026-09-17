"""Checks for the preventive RPD filter: which members the ledger says are out
of requests for today, and that routing skips them without stalling.

The unit half builds a usage directory by hand and asks the quota package what
it makes of it; the integration half puts a real `RpdBudget` over that directory
behind a real router and checks which provider comes back.

No framework: `python -m tests.llm_router.test_rpd_filter` (or run the file).
"""

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from llm_router.quota import budget
from llm_router.quota.budget import RpdBudget, exhausted_rpd
from llm_router.quota.windows import day_start
from llm_router.router import AutonomousLLMRouter

#: Midday Pacific, mid-minute -- the same instant test_quota uses. A `NOW` on a
#: day boundary would put every default call in yesterday.
NOW = 1_800_045_045
DAY = 86_400
#: When Gemini's day last turned. Everything before it is spent budget the
#: vendor has already forgiven.
MIDNIGHT = day_start("gemini", NOW)

# Two accounts serving one wide model on a 20-a-day ceiling -- the shape that
# motivated the filter -- plus a workhorse with a thousand, and one member
# publishing no daily ceiling at all.
POOL = {
    "generated": NOW,
    "config": "llm_router/config.yaml",
    "pool": [
        {"provider": "Flash_gemini_1", "account": "gemini_1", "platform": "gemini",
         "model": "gemini-3.7-flash", "priority": 2, "max_input_tokens": 250000,
         "limits": {"rpm": 5, "tpm": 250000, "rpd": 20}},
        {"provider": "Flash_gemini_2", "account": "gemini_2", "platform": "gemini",
         "model": "gemini-3.7-flash", "priority": 2, "max_input_tokens": 250000,
         "limits": {"rpm": 5, "tpm": 250000, "rpd": 20}},
        {"provider": "GptOss_groq_1", "account": "groq_1", "platform": "groq",
         "model": "openai/gpt-oss-120b", "priority": 4, "max_input_tokens": 8000,
         "limits": {"rpm": 30, "tpm": 8000, "rpd": 1000, "tpd": 100000}},
        {"provider": "Gemma_gemini_1", "account": "gemini_1", "platform": "gemini",
         "model": "gemma-4-31b-it", "priority": 31, "max_input_tokens": 128000,
         "limits": {"rpm": 15}},
    ],
}


def call(provider, ts=NOW - 10, outcome="ok", quota_window=None):
    entry = {"ts": ts, "provider": provider, "outcome": outcome}
    if outcome != "rate_limited":
        entry["tokens_in"], entry["tokens_out"] = 1000, 200
    if quota_window:
        entry["quota_window"] = quota_window
    return entry


def usage_dir(calls, pool=POOL):
    """A throwaway `.usage/` holding exactly these lines."""
    directory = Path(tempfile.mkdtemp())
    (directory / "ledger.jsonl").write_text(
        "".join(json.dumps(entry) + "\n" for entry in calls), encoding="utf-8")
    if pool is not None:
        (directory / "pool.json").write_text(json.dumps(pool), encoding="utf-8")
    return directory


class _FakeProvider:
    """Minimal stand-in exposing what get_best_provider() reads."""

    def __init__(self, name, priority, max_input_tokens, available=True,
                 limits=None, platform="gemini"):
        self.name = name
        self.priority = priority
        self.max_input_tokens = max_input_tokens
        self._available = available
        self.limits = limits or {}
        self.platform = platform
        self.cooldowns = []

    def check_availability(self):
        return self._available

    def trigger_cooldown(self, seconds=None):
        self.cooldowns.append(seconds)
        self._available = False


def _check_exhaustion():
    """What the ledger has to say about a day of requests."""
    # Nineteen of twenty is not exhausted; the twentieth is.
    nineteen = usage_dir([call("Flash_gemini_1") for _ in range(19)])
    assert exhausted_rpd(nineteen, NOW) == set()
    twenty = usage_dir([call("Flash_gemini_1") for _ in range(20)])
    assert exhausted_rpd(twenty, NOW) == {"Flash_gemini_1"}

    # One account being spent says nothing about the other, or about a model
    # whose ceiling is 1000 -- windows never mix (14.7).
    both = usage_dir([call("Flash_gemini_1") for _ in range(25)]
                     + [call("Flash_gemini_2") for _ in range(3)]
                     + [call("GptOss_groq_1") for _ in range(50)])
    assert exhausted_rpd(both, NOW) == {"Flash_gemini_1"}

    # An unclassified refusal might be RPM/TPM/capacity and must not poison the
    # daily estimate. An explicit RPD refusal is authoritative immediately.
    refused = usage_dir([call("Flash_gemini_1") for _ in range(18)]
                        + [call("Flash_gemini_1", outcome="rate_limited"),
                           call("Flash_gemini_1", outcome="rate_limited")])
    assert exhausted_rpd(refused, NOW) == set()
    explicit = usage_dir([call("Flash_gemini_1", outcome="rate_limited",
                               quota_window="rpd")])
    assert exhausted_rpd(explicit, NOW) == {"Flash_gemini_1"}

    # An attempt that never reached the provider spent nothing at all.
    unanswered = [dict(call("Flash_gemini_1"), reached=False) for _ in range(20)]
    assert exhausted_rpd(usage_dir(unanswered), NOW) == set()

    # Yesterday is out of the window: the budget has rolled.
    stale = usage_dir([call("Flash_gemini_1", ts=NOW - DAY - 60) for _ in range(30)])
    assert exhausted_rpd(stale, NOW) == set()

    # And "yesterday" is the vendor's, not a rolling twenty-four hours. A day
    # spent right up to midnight Pacific is forgiven the moment it passes, so a
    # member is available again hours before a rolling window would say so --
    # the error this filter must not make is refusing to route to a member
    # Google would have served.
    turned = usage_dir([call("Flash_gemini_1", ts=MIDNIGHT - 60)
                        for _ in range(30)])
    assert exhausted_rpd(turned, NOW) == set(), "midnight Pacific cleared it"
    assert exhausted_rpd(turned, MIDNIGHT - 1) == {"Flash_gemini_1"},         "and a second earlier it was spent"

    # Groq turns its day eight hours later, so the same ledger reads differently
    # for the two platforms. Nothing here has one day of its own.
    groq_midnight = day_start("groq", NOW)
    across = usage_dir([call("GptOss_groq_1", ts=groq_midnight - 60)
                        for _ in range(1000)])
    assert exhausted_rpd(across, NOW) == set()

    # A member that publishes no daily ceiling can never be filtered out, no
    # matter how much it has served.
    unbounded = usage_dir([call("Gemma_gemini_1") for _ in range(5000)])
    assert exhausted_rpd(unbounded, NOW) == set()

    # No snapshot means no limits to measure against -- everything is available,
    # which is the direction this is allowed to be wrong in.
    no_pool = usage_dir([call("Flash_gemini_1") for _ in range(50)], pool=None)
    assert exhausted_rpd(no_pool, NOW) == set()

    # A directory with no ledger at all is an empty day, not an error.
    assert exhausted_rpd(Path(tempfile.mkdtemp()), NOW) == set()

    print("  exhaustion: ok")


def _check_budget():
    """The object the router holds: caching, the off switch, and failure."""
    current = time.time() - 10
    directory = usage_dir([call("Flash_gemini_1", ts=current) for _ in range(20)])
    spent = _FakeProvider("Flash_gemini_1", priority=2, max_input_tokens=250000)
    fresh = _FakeProvider("Flash_gemini_2", priority=2, max_input_tokens=250000)

    quota = RpdBudget(directory=directory, ttl=1000)
    assert not quota.has_budget(spent)
    assert quota.has_budget(fresh)

    # Inside the TTL the reading is reused: the ledger has moved on, the answer
    # has not. Overspending here is deliberate -- it lands on the retry path.
    with (directory / "ledger.jsonl").open("a", encoding="utf-8") as fh:
        for _ in range(20):
            fh.write(json.dumps(call("Flash_gemini_2", ts=current)) + "\n")
    assert quota.has_budget(fresh), "a cached reading should not be re-read"

    # A budget that never caches sees the same file differently.
    assert not RpdBudget(directory=directory, ttl=0).has_budget(fresh)

    # The off switch routes as though none of this existed.
    assert RpdBudget(directory=directory, enabled=False).has_budget(spent)

    # If the ledger cannot be read at all, every member is available.
    broken = RpdBudget(directory=directory, ttl=0)
    original = budget.read_ledger

    def _raise(*args, **kwargs):
        raise ValueError("torn ledger")

    budget.read_ledger = _raise
    try:
        assert broken.has_budget(spent)
        assert broken.exhausted() == set()
    finally:
        budget.read_ledger = original

    # A provider object with no name at all is not something to skip.
    assert quota.has_budget(object())

    print("  budget: ok")


class _StubQuota:
    """A fixed answer, so routing can be checked without a ledger."""

    def __init__(self, *exhausted):
        self.spent = set(exhausted)

    def has_budget(self, provider):
        return provider.name not in self.spent


def _check_routing():
    """Selection skips a spent member, and never stalls on its account."""
    flash = _FakeProvider("flash", priority=2, max_input_tokens=250000)
    mid = _FakeProvider("mid", priority=11, max_input_tokens=30000)
    small = _FakeProvider("small", priority=20, max_input_tokens=6000)

    # Nothing spent: priority decides, exactly as before.
    plenty = AutonomousLLMRouter([flash, mid, small], quota=_StubQuota())
    assert plenty.get_best_provider() is flash

    # The top-priority member is out of requests for today, so it is passed over
    # silently and the next one serves.
    router = AutonomousLLMRouter([flash, mid, small], quota=_StubQuota("flash"))
    assert router.get_best_provider() is mid

    # A retry preference must not rediscover a member whose daily budget is
    # already spent. A recovered, funded member is preferable even if this
    # request tried it earlier.
    recovered = _FakeProvider("recovered", priority=2, max_input_tokens=250000)
    spent_fresh = _FakeProvider("spent_fresh", priority=1, max_input_tokens=250000)
    retry = AutonomousLLMRouter([recovered, spent_fresh],
                                quota=_StubQuota("spent_fresh"))
    assert retry.get_best_provider(attempted={recovered.name}) is recovered

    # The filter stacks with size: `mid` is spent and `small` cannot hold the
    # request, so the only member that is both funded and big enough wins.
    stacked = AutonomousLLMRouter([flash, mid, small], quota=_StubQuota("mid"))
    assert stacked.get_best_provider(20000) is flash

    # ...and with cooldown, which is a different kind of unavailable.
    flash._available = False
    assert stacked.get_best_provider() is small
    flash._available = True

    # Everything spent: attempt the call anyway rather than stall. The count is
    # approximate, and a refusal is cheaper than a dead run.
    empty = AutonomousLLMRouter([flash, mid, small],
                                quota=_StubQuota("flash", "mid", "small"))
    assert empty.get_best_provider() is flash
    assert empty.get_best_provider(20000) is flash

    # A pool in cooldown still reports None, and the filter has not changed what
    # the caller then waits for.
    for provider in (flash, mid, small):
        provider._available = False
    assert empty.get_best_provider() is None

    print("  routing: ok")


def _check_tpm():
    """Accepted tokens, not refusals, prevent a predictable TPM overflow."""
    current = time.time() - 2
    directory = usage_dir([
        {**call("full", ts=current), "tokens_in": 8_000},
        call("full", ts=current, outcome="rate_limited"),
    ])
    full = _FakeProvider("full", 1, 100_000, limits={"tpm": 10_000})
    fresh = _FakeProvider("fresh", 2, 100_000, limits={"tpm": 10_000})
    router = AutonomousLLMRouter([full, fresh],
                                 quota=RpdBudget(directory=directory, ttl=0))
    assert router.get_best_provider(2_000) is fresh

    with (directory / "ledger.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({**call("fresh", ts=current), "tokens_in": 8_000}) + "\n")
    assert router.get_best_provider(2_000) is None
    assert full.cooldowns and fresh.cooldowns
    print("  tpm: ok")


def _check_integration():
    """A real budget over a real usage directory, behind a real router."""
    current = time.time() - 10
    directory = usage_dir([call("Flash_gemini_1", ts=current) for _ in range(20)]
                          + [call("Flash_gemini_2", ts=current) for _ in range(2)])
    first = _FakeProvider("Flash_gemini_1", priority=2, max_input_tokens=250000)
    second = _FakeProvider("Flash_gemini_2", priority=2, max_input_tokens=250000)
    groq = _FakeProvider("GptOss_groq_1", priority=4, max_input_tokens=8000)

    router = AutonomousLLMRouter([first, second, groq],
                                 quota=RpdBudget(directory=directory, ttl=0))
    # Same model, same priority, two accounts: the one with budget left serves.
    assert router.get_best_provider() is second

    # Spend the second account too, and the pool falls through to the model that
    # still has a thousand requests a day.
    with (directory / "ledger.jsonl").open("a", encoding="utf-8") as fh:
        for _ in range(20):
            fh.write(json.dumps(call("Flash_gemini_2", ts=current)) + "\n")
    assert router.get_best_provider() is groq

    # A request too large for what is left routes to the largest window there
    # is, spent or not -- refusing to answer is not one of the options.
    assert router.get_best_provider(100000) in (first, second)

    print("  integration: ok")


def _run():
    _check_exhaustion()
    _check_budget()
    _check_routing()
    _check_tpm()
    _check_integration()
    print("rpd_filter: all checks passed")


# pytest collects `test_*` functions, not `_run` (see test_size_routing.py).
def test_rpd_filter():
    _run()


if __name__ == "__main__":
    _run()
