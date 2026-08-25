"""Turning attempts into the question a human actually asks: how close is each
free account to the wall, and when does it clear.

Everything here is derived from `ledger.jsonl`. Nothing is fetched, and no
figure is a vendor's. That is a real limit and is stated on the panel rather
than hidden: **the ledger sees only what went through this router**, so a key
also used from another machine, or by hand, is under-counted here.

## The window model

A budget is assumed to work like this: the first request starts the window, and
the window ends one length later, whatever happened in between. So to know when
the current window began, look back one length from now and take the *oldest*
attempt in that span -- that attempt opened it, and the budget resets one length
after it.

That is why `used` and `resets_in` come from the same span: every attempt in the
last minute belongs to the same minute-window, because the oldest of them opened
it no earlier than a minute ago.

The assumption is not free. A vendor running a leaky bucket (Groq's request
budget refills continuously) clears earlier than this predicts, and one running
a calendar day (Gemini resets at midnight Pacific) clears at a time this cannot
know. It errs toward saying a budget is still spent, which is the safe
direction: it will not promise headroom that isn't there. Where the provider
told us better -- a `Retry-After` on a refusal -- that is used instead.

## Two views of the same ledger

A model is fanned across every account on its platform, so `gemini-3.5-flash`
may be three pool members. Each is its own budget with its own clock, and the
`Row` for it is one account × one model -- windows are never mixed.

But the question that made this panel worth building is *how much Gemini do I
have left*, not *how much is left on key two*. So the rows are also folded into
a `ModelSummary` per platform × model: capacity adds up across accounts (three
keys at 20 requests a day are 60 requests a day), and consumption adds up with
it. That is the default view; the per-account rows are what you filter down to
once you know which model is running out.

Nothing is summed across *platforms*, and no platform is given a total ceiling:
Groq meters one org-wide request budget across every model on an account, so
adding its per-model limits together would invent capacity that does not exist
([3.4](../../docs/03-pool-model.md#34-priority-tiers)).
"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

MINUTE_SECONDS = 60
DAY_SECONDS = 86_400

#: Which window each declared limit is measured over.
WINDOWS = {"rpm": MINUTE_SECONDS, "tpm": MINUTE_SECONDS,
           "rpd": DAY_SECONDS, "tpd": DAY_SECONDS}
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
    #: The attempt that opened this window, and the oldest that spent tokens.
    #: They differ because a refusal spends a request and no tokens.
    opened: Optional[float] = None
    opened_tokens: Optional[float] = None

    @property
    def tokens(self) -> int:
        """Prompt and reply together: every free tier meters both as one."""
        return self.tokens_in + self.tokens_out

    def add(self, call: dict) -> None:
        timestamp = call.get("ts") or 0.0
        # An attempt the provider never answered spent nothing and opens no
        # window. The router also never records a member it skipped on size --
        # the ledger holds attempts, not intentions -- so what is counted here
        # is exactly what an account was asked to serve.
        if call.get("reached", True) is False:
            self.unanswered += 1
            self.errors += 1
            return

        self.requests += 1
        self.opened = timestamp if self.opened is None else min(self.opened, timestamp)

        spent = (call.get("tokens_in") or 0) + (call.get("tokens_out") or 0)
        self.tokens_in += call.get("tokens_in") or 0
        self.tokens_out += call.get("tokens_out") or 0
        if spent:
            self.opened_tokens = (timestamp if self.opened_tokens is None
                                  else min(self.opened_tokens, timestamp))

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
        for name in ("opened", "opened_tokens"):
            mine, theirs = getattr(self, name), getattr(other, name)
            if theirs is not None:
                setattr(self, name, theirs if mine is None else min(mine, theirs))


@dataclass
class Gauge:
    """One metered quantity, and the ceiling it is spending against."""

    name: str
    used: int
    #: None when nothing declares a ceiling (Gemma's TPM is unlimited).
    limit: Optional[int] = None
    ratio: Optional[float] = None
    #: Seconds until this window's budget resets, or None if nothing is in it.
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
    limits: dict
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
    pool_generated: Optional[float] = None
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


def _gauges_for(limits: dict, minute: Usage, day: Usage, now: float) -> List[Gauge]:
    """A gauge per metered quantity, declared ceiling or not.

    An undeclared limit still has consumption worth showing -- dropping the
    gauge would leave Gemma's token use nowhere on the page.
    """
    gauges = []
    for name in _LIMIT_NAMES:
        window = WINDOWS[name]
        usage = minute if window == MINUTE_SECONDS else day
        counts_requests = name.startswith("r")

        used = usage.requests if counts_requests else usage.tokens
        # A refusal opens the request window (it spent a request) but not the
        # token window (it spent none), so each asks the clock it belongs to.
        opened = usage.opened if counts_requests else usage.opened_tokens

        declared = limits.get(name)
        limit = declared if isinstance(declared, int) and declared > 0 else None
        gauges.append(Gauge(
            name=name,
            used=used,
            limit=limit,
            ratio=None if limit is None else used / limit,
            resets_in=None if opened is None else max(0.0, opened + window - now),
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

    The reset is the *soonest* of the accounts' windows, because that is when
    capacity next appears, whichever key it appears on.
    """
    folded: Dict[tuple, ModelSummary] = {}
    for row in sorted(rows, key=lambda row: row.account):
        key = (row.platform, row.model)
        summary = folded.setdefault(key, ModelSummary(model=row.model,
                                                      platform=row.platform,
                                                      priority=row.priority))
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
                                            resets_in=gauge.resets_in,
                                            refused=gauge.refused))
                continue
            existing.used += gauge.used
            existing.refused += gauge.refused
            existing.limit = (None if existing.limit is None or gauge.limit is None
                              else existing.limit + gauge.limit)
            if gauge.resets_in is not None and gauge.used:
                existing.resets_in = (gauge.resets_in if existing.resets_in is None
                                      else min(existing.resets_in, gauge.resets_in))

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


def _fold_platforms(models: List[ModelSummary],
                    accounts: List[AccountSummary]) -> List[PlatformSummary]:
    """What each platform served today. No ceiling: see the module docstring."""
    platforms: Dict[str, PlatformSummary] = {}
    for summary in models:
        platform = platforms.setdefault(summary.platform,
                                        PlatformSummary(platform=summary.platform))
        platform.models += 1
        platform.minute.merge(summary.minute)
        platform.day.merge(summary.day)
        platform.total.merge(summary.total)
    for account in accounts:
        platform = platforms.get(account.platform)
        if platform is not None and account.account not in platform.accounts:
            platform.accounts.append(account.account)
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

    def row_for(provider: str, call: Optional[dict] = None) -> Row:
        if provider in rows:
            return rows[provider]
        member = members.get(provider)
        source = member or call or {}
        rows[provider] = Row(
            provider=provider,
            account=source.get("account", "unknown"),
            platform=source.get("platform", "unknown"),
            model=source.get("model", "unknown"),
            priority=(member or {}).get("priority"),
            limits=(member or {}).get("limits") or {},
            configured=member is not None,
        )
        return rows[provider]

    # Every configured member gets a row even having spent nothing: untouched
    # budget is exactly what someone deciding whether to start a long run wants
    # to see, and a panel listing only what had been used would hide it.
    for provider in members:
        row_for(provider)

    since: Optional[float] = None
    for call in calls:
        timestamp = call.get("ts") or 0.0
        if since is None or timestamp < since:
            since = timestamp
        provider = call.get("provider", "unknown")
        row = row_for(provider, call)
        age = now - timestamp
        row.total.add(call)
        if age <= DAY_SECONDS:
            row.day.add(call)
        if age <= MINUTE_SECONDS:
            row.minute.add(call)
        if row.last_call is None or timestamp > row.last_call:
            row.last_call = timestamp
        if call.get("outcome") == "rate_limited":
            refusals.setdefault(provider, []).append(call)

    accounts: Dict[str, AccountSummary] = {}
    for row in rows.values():
        row.gauges = _gauges_for(row.limits, row.minute, row.day, now)
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
        pool_generated=(pool or {}).get("generated"),
        config=(pool or {}).get("config"),
        since=since,
        calls=len(calls),
        rows=sorted(rows.values(), key=lambda row: (
            row.platform, row.account,
            row.priority if row.priority is not None else 999, row.model)),
        accounts=sorted(accounts.values(), key=lambda a: (a.platform, a.account)),
    )
    report.models = _fold_models(report.rows)
    report.platforms = _fold_platforms(report.models, report.accounts)
    report.notes = _notes(report, pool)
    return report


def _notes(report: Report, pool: Optional[dict]) -> List[str]:
    notes = []
    if pool is None:
        notes.append("No pool snapshot found, so there are no limits to measure "
                     "against. Run `python -m llm_router` to write one.")
    if report.calls == 0:
        notes.append("The ledger is empty: nothing recorded since it was last cleared.")

    refused = sum(row.day.rate_limited for row in report.rows)
    if refused:
        notes.append(f"{refused} attempt(s) in the last 24h were refused for quota. "
                     "They count as requests here -- they spent one -- but as no "
                     "tokens, because none were.")

    orphans = [row.provider for row in report.rows if not row.configured]
    if orphans:
        notes.append(f"{len(orphans)} member(s) in the ledger are no longer in the pool "
                     f"({', '.join(orphans)}); their limits are unknown.")

    unbounded = [row for row in report.rows if row.configured and row.tightest is None]
    if unbounded:
        notes.append(f"{len(unbounded)} configured member(s) declare no limits in "
                     "config.yaml, so their consumption is reported without a ceiling.")
    return notes
