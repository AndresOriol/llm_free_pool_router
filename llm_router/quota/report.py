"""Turning attempts into the question a human actually asks: how close is each
free account to the wall, and when does it clear.

Everything is derived from `ledger.jsonl` -- nothing is fetched, so the ledger's
blind spot is the report's: a key used outside this router is under-counted.

Two assumptions carry the arithmetic, both argued in
[14. Quota panel](../../docs/14-quota-panel.md):

- a window is a bucket on the *vendor's* clock -- Gemini's day ends at midnight
  Pacific, its minute when the wall clock's does ([windows.py](windows.py),
  14.5) -- and
- a refused attempt spent a request and no tokens; one that got no answer spent
  neither (14.6).

Rows are one account x model, because windows never mix. `ModelSummary` folds
them across accounts -- the default view -- and nothing is ever summed across
platforms (14.7).
"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .windows import (MINUTE_SECONDS, WINDOWS, day_start, minute_start,
                      resets_in, tokens_metered)

_LIMIT_NAMES = ("rpm", "tpm", "rpd", "tpd")


@dataclass
class Usage:
    """What happened inside one window."""

    requests: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    #: Attempts the provider refused for want of quota. Counted as requests,
    #: because the provider answered: it read the request and said no.
    rate_limited: int = 0
    errors: int = 0
    #: Attempts that never got an answer -- a timeout, a connection that failed.
    #: Counted nowhere else: the account was never asked, so it paid nothing.
    unanswered: int = 0

    @property
    def tokens(self) -> int:
        """Prompt and reply together, for a platform that meters both as one."""
        return self.tokens_in + self.tokens_out

    def metered_tokens(self, platform: Optional[str]) -> int:
        """The tokens this platform's meter actually counts.

        Gemini publishes its tokens-per-minute over *input* tokens, so charging
        the reply against that ceiling inflates every token gauge on the
        platform the pool leans on hardest ([windows.py](windows.py)).
        """
        return (self.tokens_in if tokens_metered(platform) == "input"
                else self.tokens)

    def add(self, call: dict) -> None:
        # An attempt the provider never answered spent nothing and opens no
        # window. The router also never records a member it skipped on size --
        # the ledger holds attempts, not intentions -- so what is counted here
        # is exactly what an account was asked to serve.
        if call.get("reached", True) is False:
            self.unanswered += 1
            self.errors += 1
            return

        self.requests += 1
        self.tokens_in += call.get("tokens_in") or 0
        self.tokens_out += call.get("tokens_out") or 0

        if call.get("outcome") == "rate_limited":
            self.rate_limited += 1
        elif call.get("outcome") == "error":
            self.errors += 1

    def merge(self, other: "Usage") -> None:
        self.requests += other.requests
        self.tokens_in += other.tokens_in
        self.tokens_out += other.tokens_out
        self.rate_limited += other.rate_limited
        self.errors += other.errors
        self.unanswered += other.unanswered


@dataclass
class Gauge:
    """One metered quantity, and the ceiling it is spending against."""

    name: str
    used: int
    #: None when nothing declares a ceiling (Gemma's TPM is unlimited). Folded
    #: across accounts this is their ceilings added together.
    limit: Optional[int] = None
    #: How many accounts' ceilings went into `limit`. Above one, the panel shows
    #: the sum as what it is -- 2 x 5, not a mystery 10.
    sources: int = 0
    ratio: Optional[float] = None
    #: Seconds until the vendor's own window turns over, or None if this one
    #: holds nothing to clear. Read off the calendar, not off our history.
    resets_in: Optional[float] = None
    #: How much of `used` was attempts the provider refused.
    refused: int = 0


@dataclass
class Row:
    provider: str
    account: str
    platform: str
    model: str
    priority: Optional[int]
    #: The per-request token ceiling the router routes by, for the same reason
    #: it matters there: a member that cannot hold the job is not capacity.
    max_input_tokens: Optional[int] = None
    minute: Usage = field(default_factory=Usage)
    day: Usage = field(default_factory=Usage)
    total: Usage = field(default_factory=Usage)
    gauges: List[Gauge] = field(default_factory=list)
    #: The gauge closest to its ceiling: what will stop this member first.
    tightest: Optional[Gauge] = None
    last_call: Optional[float] = None
    #: When the provider's own Retry-After says this member frees up, if it is
    #: still in the future. Beats anything derived from the window model.
    blocked_for: Optional[float] = None
    #: False when the ledger has calls for a member the config no longer has.
    configured: bool = True


@dataclass
class AccountSummary:
    account: str
    platform: str
    members: int = 0
    minute: Usage = field(default_factory=Usage)
    day: Usage = field(default_factory=Usage)
    total: Usage = field(default_factory=Usage)
    #: The soonest a refused member on this account is due back.
    blocked_for: Optional[float] = None


@dataclass
class ModelSummary:
    """One model across every account that serves it."""

    model: str
    platform: str
    #: The accounts backing it, in the order they appear in the pool.
    accounts: List[str] = field(default_factory=list)
    priority: Optional[int] = None
    max_input_tokens: Optional[int] = None
    minute: Usage = field(default_factory=Usage)
    day: Usage = field(default_factory=Usage)
    total: Usage = field(default_factory=Usage)
    #: Gauges whose limits are the accounts' limits added together.
    gauges: List[Gauge] = field(default_factory=list)
    tightest: Optional[Gauge] = None
    last_call: Optional[float] = None
    #: The soonest any of its accounts is due back from a refusal.
    blocked_for: Optional[float] = None


@dataclass
class PlatformSummary:
    """One platform: what it has served today, over all its accounts.

    Deliberately without a ceiling of its own -- see the module docstring.
    """

    platform: str
    accounts: List[str] = field(default_factory=list)
    models: int = 0
    minute: Usage = field(default_factory=Usage)
    day: Usage = field(default_factory=Usage)
    total: Usage = field(default_factory=Usage)


@dataclass
class Report:
    generated: float
    config: Optional[str] = None
    #: Timestamp of the oldest call on record, or None on an empty ledger.
    since: Optional[float] = None
    calls: int = 0
    #: One account x model each: the finest view, and the one windows live in.
    rows: List[Row] = field(default_factory=list)
    #: The same rows folded across accounts. The default view.
    models: List[ModelSummary] = field(default_factory=list)
    accounts: List[AccountSummary] = field(default_factory=list)
    platforms: List[PlatformSummary] = field(default_factory=list)
    #: What a reader should know before believing the numbers.
    notes: List[str] = field(default_factory=list)


def declared_limits(entries) -> tuple:
    """Which of the four metered quantities anything here declares a ceiling for.

    A column nobody has a limit for is a column of "no cap": Gemini publishes no
    tokens-per-day for any model, so a TPD column on that table is four
    characters of heading and ten rows of nothing. Renderers ask this and drop
    the rest.
    """
    return tuple(name for name in _LIMIT_NAMES
                 if any(gauge.limit is not None
                        for entry in entries for gauge in entry.gauges
                        if gauge.name == name))


def _gauges_for(limits: dict, minute: Usage, day: Usage,
                platform: Optional[str], now: float) -> List[Gauge]:
    """A gauge per metered quantity, declared ceiling or not.

    An undeclared limit still has consumption worth showing -- dropping the
    gauge would leave Gemma's token use nowhere on the page.

    Both of a window's gauges clear together, because they are the same bucket
    on the vendor's clock: a refusal and a served call sit in the same clock
    minute whatever either one spent. What differs is what they *count* -- a
    refusal spends a request and no tokens (14.6).
    """
    gauges = []
    for name in _LIMIT_NAMES:
        usage = minute if WINDOWS[name] == MINUTE_SECONDS else day
        counts_requests = name.startswith("r")

        used = (usage.requests if counts_requests
                else usage.metered_tokens(platform))
        declared = limits.get(name)
        limit = declared if isinstance(declared, int) and declared > 0 else None
        gauges.append(Gauge(
            name=name,
            used=used,
            limit=limit,
            sources=1 if limit is not None else 0,
            ratio=None if limit is None else used / limit,
            resets_in=None if not used else resets_in(name, platform, now),
            refused=usage.rate_limited if counts_requests else 0,
        ))
    return gauges


def _blocked_for(calls: List[dict], now: float) -> Optional[float]:
    """Seconds until the provider itself said it would take us back."""
    until = [call["ts"] + call["retry_after"] for call in calls
             if call.get("outcome") == "rate_limited" and call.get("retry_after")]
    soonest = max(until, default=None)
    return round(soonest - now, 1) if soonest and soonest > now else None


def _fold_models(rows: List[Row]) -> List[ModelSummary]:
    """Rows folded across the accounts that serve the same model.

    Capacity adds because each account is a separate budget: two keys at 20
    requests a day really are 40 requests a day. A limit only one account
    declares is not summed into a total -- an unknown ceiling anywhere makes the
    total unknown, and a made-up number here would be worse than none.

    Every row in a summary is one platform, so they share a reset: the vendor's
    clock turns for all its keys at once.
    """
    folded: Dict[tuple, ModelSummary] = {}
    for row in sorted(rows, key=lambda row: row.account):
        key = (row.platform, row.model)
        summary = folded.setdefault(key, ModelSummary(
            model=row.model, platform=row.platform, priority=row.priority,
            max_input_tokens=row.max_input_tokens))
        summary.accounts.append(row.account)
        summary.minute.merge(row.minute)
        summary.day.merge(row.day)
        summary.total.merge(row.total)
        if row.last_call is not None:
            summary.last_call = (row.last_call if summary.last_call is None
                                 else max(summary.last_call, row.last_call))
        if row.blocked_for is not None:
            summary.blocked_for = (row.blocked_for if summary.blocked_for is None
                                   else min(summary.blocked_for, row.blocked_for))

        for gauge in row.gauges:
            existing = next((candidate for candidate in summary.gauges
                             if candidate.name == gauge.name), None)
            if existing is None:
                summary.gauges.append(Gauge(name=gauge.name, used=gauge.used,
                                            limit=gauge.limit,
                                            sources=gauge.sources,
                                            resets_in=gauge.resets_in,
                                            refused=gauge.refused))
                continue
            existing.used += gauge.used
            existing.refused += gauge.refused
            existing.sources += gauge.sources
            existing.limit = (None if existing.limit is None or gauge.limit is None
                              else existing.limit + gauge.limit)
            if existing.resets_in is None:
                existing.resets_in = gauge.resets_in

    for summary in folded.values():
        for gauge in summary.gauges:
            gauge.ratio = None if not gauge.limit else gauge.used / gauge.limit
            if not gauge.used:
                gauge.resets_in = None
        with_limits = [gauge for gauge in summary.gauges if gauge.ratio is not None]
        summary.tightest = max(with_limits, key=lambda gauge: gauge.ratio, default=None)

    return sorted(folded.values(), key=lambda summary: (
        summary.platform,
        summary.priority if summary.priority is not None else 999,
        summary.model))


def _fold_platforms(models: List[ModelSummary]) -> List[PlatformSummary]:
    """What each platform served today. No ceiling: see the module docstring."""
    platforms: Dict[str, PlatformSummary] = {}
    for summary in models:
        platform = platforms.setdefault(summary.platform,
                                        PlatformSummary(platform=summary.platform))
        platform.models += 1
        platform.minute.merge(summary.minute)
        platform.day.merge(summary.day)
        platform.total.merge(summary.total)
        for account in summary.accounts:
            if account not in platform.accounts:
                platform.accounts.append(account)
    return sorted(platforms.values(), key=lambda platform: platform.platform)


def build_report(calls: List[dict], pool: Optional[dict],
                 now: Optional[float] = None) -> Report:
    """Build the whole report.

    `pool` may be None -- the panel still reports what was consumed, it just has
    nothing to measure it against.
    """
    now = time.time() if now is None else now
    members = {member["provider"]: member for member in (pool or {}).get("pool", [])}

    rows: Dict[str, Row] = {}
    refusals: Dict[str, List[dict]] = {}
    limits: Dict[str, dict] = {}

    def row_for(provider: str, call: Optional[dict] = None) -> Row:
        if provider in rows:
            return rows[provider]
        member = members.get(provider)
        source = member or call or {}
        limits[provider] = (member or {}).get("limits") or {}
        rows[provider] = Row(
            provider=provider,
            account=source.get("account", "unknown"),
            platform=source.get("platform", "unknown"),
            model=source.get("model", "unknown"),
            priority=(member or {}).get("priority"),
            max_input_tokens=(member or {}).get("max_input_tokens"),
            configured=member is not None,
        )
        return rows[provider]

    # Every configured member gets a row even having spent nothing: untouched
    # budget is exactly what someone deciding whether to start a long run wants
    # to see, and a panel listing only what had been used would hide it.
    for provider in members:
        row_for(provider)

    since: Optional[float] = None
    # The edges the vendors put their meters on: this clock minute, and each
    # platform's day as it turns in the vendor's own timezone (windows.py).
    this_minute = minute_start(now)
    today: Dict[Optional[str], float] = {}
    for call in calls:
        timestamp = call.get("ts") or 0.0
        if since is None or timestamp < since:
            since = timestamp
        provider = call.get("provider", "unknown")
        row = row_for(provider, call)
        row.total.add(call)
        if row.platform not in today:
            today[row.platform] = day_start(row.platform, now)
        if timestamp >= today[row.platform]:
            row.day.add(call)
        if timestamp >= this_minute:
            row.minute.add(call)
        if row.last_call is None or timestamp > row.last_call:
            row.last_call = timestamp
        if call.get("outcome") == "rate_limited":
            refusals.setdefault(provider, []).append(call)

    accounts: Dict[str, AccountSummary] = {}
    for row in rows.values():
        row.gauges = _gauges_for(limits[row.provider], row.minute, row.day,
                                 row.platform, now)
        with_limits = [gauge for gauge in row.gauges if gauge.ratio is not None]
        row.tightest = max(with_limits, key=lambda gauge: gauge.ratio, default=None)
        row.blocked_for = _blocked_for(refusals.get(row.provider, []), now)

        # Rolled up per account -- and only within one account, because that is
        # where a free tier's budget lives. Groq meters one org-wide request
        # pool across every model on the account, so the per-model rows flatter
        # it (docs/03-pool-model.md#34-priority-tiers); two accounts on the same
        # platform share nothing at all.
        key = f"{row.platform}/{row.account}"
        summary = accounts.setdefault(key, AccountSummary(row.account, row.platform))
        summary.members += 1
        summary.minute.merge(row.minute)
        summary.day.merge(row.day)
        summary.total.merge(row.total)
        if row.blocked_for is not None:
            summary.blocked_for = (row.blocked_for if summary.blocked_for is None
                                   else min(summary.blocked_for, row.blocked_for))

    report = Report(
        generated=now,
        config=(pool or {}).get("config"),
        since=since,
        calls=len(calls),
        rows=sorted(rows.values(), key=lambda row: (
            row.platform, row.account,
            row.priority if row.priority is not None else 999, row.model)),
        accounts=sorted(accounts.values(), key=lambda a: (a.platform, a.account)),
    )
    report.models = _fold_models(report.rows)
    report.platforms = _fold_platforms(report.models)
    report.notes = _notes(report, pool)
    return report


def _notes(report: Report, pool: Optional[dict]) -> List[str]:
    notes = []
    if pool is None:
        notes.append("No pool snapshot, so no limits to measure against. Run "
                     "`python -m llm_router` to write one.")
    if report.calls == 0:
        notes.append("The ledger is empty: nothing recorded since it was last cleared.")

    refused = sum(row.day.rate_limited for row in report.rows)
    if refused:
        notes.append(f"{refused} attempt(s) were refused for quota today.")

    orphans = [row.provider for row in report.rows if not row.configured]
    if orphans:
        notes.append(f"Not in the pool any more, so shown without limits: "
                     f"{', '.join(orphans)}.")

    unbounded = [row for row in report.rows if row.configured and row.tightest is None]
    if unbounded:
        notes.append(f"{len(unbounded)} member(s) declare no limits in config.yaml, "
                     "so they are shown without a ceiling.")

    return notes
