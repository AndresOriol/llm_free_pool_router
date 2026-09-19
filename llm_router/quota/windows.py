"""Where a vendor's meter puts its edges.

A free tier's budget is not a span that opens when you first call it: it is a
bucket on the vendor's clock, and it empties at a moment decided before anyone
made a request. Gemini's day ends at midnight Pacific whatever the pool did
that afternoon, and its minute ends when the wall clock's minute does.

The report used to assume the other thing -- the first attempt opens the window
and the window ends one length later -- and the ledger says that assumption is
wrong in the direction that costs a run. Across 2,765 recorded attempts, Gemini
served **35** requests to a member declared at 20 a day inside one rolling
24-hour span, and served **50** calls at moments when the rolling count already
read *spent* -- 26 under the vendor's calendar day, the remainder being the
filter's own cached reading rather than its window. A window that never resets
on the vendor's clock keeps counting yesterday evening into this morning, and
the requests-per-day filter ([budget.py](budget.py)) then skips a member Google
would have answered. That is the one error
[What it deliberately doesn't do](../../docs/pool/quota.md#what-it-deliberately-doesnt-do) says must
never happen.

So the windows here are **calendar buckets**, and a reset is a property of the
clock rather than of our history: `resets_in` no longer depends on when we
happened to start.

Two vendor facts, declared and not measured, because a ledger cannot see them:

- **When the day turns.** Google resets free-tier daily quota at midnight
  Pacific ([Current free-tier limits](../../docs/pool/providers.md#current-free-tier-limits)); Groq
  turns its day at midnight UTC. A platform nobody has declared falls back to
  UTC, which is the safe way to be wrong: an early boundary forgets yesterday
  sooner, so it over-states headroom and lands on an attempt the provider
  refuses -- the path that already works.
- **Which tokens the per-minute meter counts.** Gemini's published TPM is
  *input* tokens; Groq meters the whole exchange. Counting the reply against a
  Gemini ceiling that never saw it inflates every token gauge on the platform
  the pool leans on most.

Nothing here reads the network, and nothing here needs the ledger.
"""

import datetime
from typing import Optional

MINUTE_SECONDS = 60
DAY_SECONDS = 86_400

#: Which window each declared limit is metered over.
WINDOWS = {"rpm": MINUTE_SECONDS, "tpm": MINUTE_SECONDS,
           "rpd": DAY_SECONDS, "tpd": DAY_SECONDS}

#: The timezone whose midnight ends each platform's day. See the module
#: docstring for why an unlisted platform gets UTC.
DAILY_RESET = {"gemini": "America/Los_Angeles", "groq": "UTC"}
DEFAULT_RESET = "UTC"

#: Pacific standard time is UTC-8 and daylight time UTC-7. Used only when the
#: interpreter has no IANA database (a bare Windows Python without `tzdata`),
#: and deliberately the daylight offset: it puts the boundary an hour early in
#: winter, which over-states headroom rather than under-stating it.
_FALLBACK_OFFSETS = {"America/Los_Angeles": -7, "UTC": 0}

#: Which tokens a platform's meter counts. `input` is prompt tokens only.
TOKENS_METERED = {"gemini": "input"}
DEFAULT_TOKENS_METERED = "both"


def _zone(name: str) -> datetime.tzinfo:
    """The named zone, or a fixed offset when no zone database is installed.

    The quota package promises the standard library and nothing else, so a
    missing `tzdata` may not be an error -- it degrades to an offset that
    ignores daylight saving.
    """
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - no tz database, or no such zone
        hours = _FALLBACK_OFFSETS.get(name, 0)
        return datetime.timezone(datetime.timedelta(hours=hours))


def daily_reset_zone(platform: Optional[str]) -> datetime.tzinfo:
    return _zone(DAILY_RESET.get(platform or "", DEFAULT_RESET))


def tokens_metered(platform: Optional[str]) -> str:
    """`input` or `both`: which half of an exchange the token meter counts."""
    return TOKENS_METERED.get(platform or "", DEFAULT_TOKENS_METERED)


def day_start(platform: Optional[str], now: float) -> float:
    """The instant this platform's current day began, in epoch seconds."""
    zone = daily_reset_zone(platform)
    local = datetime.datetime.fromtimestamp(now, zone)
    midnight = datetime.datetime(local.year, local.month, local.day, tzinfo=zone)
    return midnight.timestamp()


def minute_start(now: float) -> float:
    """The instant the current clock minute began.

    Every timezone in use shares its minute boundary with UTC, so this needs no
    zone: a per-minute quota turns over when the wall clock's seconds do.
    """
    return now - (now % MINUTE_SECONDS)


def window_start(name: str, platform: Optional[str], now: float) -> float:
    """When the bucket `name` is being metered in opened."""
    if WINDOWS[name] == MINUTE_SECONDS:
        return minute_start(now)
    return day_start(platform, now)


def resets_in(name: str, platform: Optional[str], now: float) -> float:
    """Seconds until that bucket empties. A fact about the clock, not about us."""
    return max(0.0, window_start(name, platform, now) + WINDOWS[name] - now)
