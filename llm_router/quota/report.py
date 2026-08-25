"""Turning attempts and vendor readings into the question a human actually asks:
how close is each free account to the wall, right now.

**Two sources, and they are not equal.** A vendor reading (probe.py) is what the
account has left according to the account's owner; the local ledger is what this
router happens to have spent. Where a vendor reading exists it wins, and every
gauge says which of the two it came from -- a panel that silently mixed them
would be worse than one that only counted locally, because it would look
authoritative while being neither.

**The local windows are rolling, and the vendors' are not.** Groq's request
budget refills continuously (its reset header reads `1m26.4s` against a
1,000-request limit, not "at midnight"); Gemini resets daily on Pacific time.
Encoding a bucket policy per vendor is a thing to get silently wrong, so the
local view doesn't try: RPM/TPM are the last 60 seconds and RPD/TPD the last 24
hours, always. That over-reports just after a vendor's own reset and never
claims headroom that isn't there -- the only safe direction to be wrong in, and
the reason the local count is a fallback rather than the answer.
"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

MINUTE_SECONDS = 60
DAY_SECONDS = 86_400

# The order limits are quoted in everywhere else: per-minute then per-day.
_LIMIT_NAMES = ("rpm", "tpm", "rpd", "tpd")


@dataclass
class Usage:
    requests: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    rate_limited: int = 0
    errors: int = 0

    @property
    def tokens(self) -> int:
        """Prompt and reply together: every free tier meters both as one."""
        return self.tokens_in + self.tokens_out

    def add(self, call: dict) -> None:
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


@dataclass
class Gauge:
    """One metered quantity, and the ceiling it is spending against."""

    name: str
    used: int
    #: None when the vendor declares no ceiling (Gemma's TPM).
    limit: Optional[int] = None
    #: Above 1 when a rolling local window has outrun the vendor's own.
    ratio: Optional[float] = None
    #: "vendor" (the account's own count) or "local" (this router's ledger).
    source: str = "local"
    #: Seconds until this budget refills, when the vendor said so.
    resets_in: Optional[float] = None


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
    #: False when the ledger has calls for a member the config no longer has.
    configured: bool = True
    #: The last vendor reading for this member, as probe.py wrote it.
    vendor: Optional[dict] = None


@dataclass
class AccountSummary:
    account: str
    platform: str
    members: int = 0
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
    #: When the vendors were last asked, or None if never.
    probed: Optional[float] = None
    rows: List[Row] = field(default_factory=list)
    accounts: List[AccountSummary] = field(default_factory=list)
    #: What a reader should know before believing the numbers.
    notes: List[str] = field(default_factory=list)


def _local_gauges(limits: dict, minute: Usage, day: Usage) -> List[Gauge]:
    """A gauge per metered quantity, declared ceiling or not.

    An undeclared limit still has consumption worth showing -- dropping the
    gauge would leave Gemma's token use nowhere on the page.
    """
    measured = {"rpm": minute.requests, "tpm": minute.tokens,
                "rpd": day.requests, "tpd": day.tokens}
    gauges = []
    for name in _LIMIT_NAMES:
        declared = limits.get(name)
        limit = declared if isinstance(declared, int) and declared > 0 else None
        used = measured[name]
        gauges.append(Gauge(name=name, used=used, limit=limit,
                            ratio=None if limit is None else used / limit))
    return gauges


def _vendor_gauges(limits: dict, vendor: dict) -> Dict[str, Gauge]:
    """What the vendor said, mapped onto the limit it belongs to.

    Which window a vendor's header describes is not fixed by the header name:
    Groq's `x-ratelimit-limit-requests` is the daily budget while its
    `-limit-tokens` is the per-minute one. Rather than hard-code that per
    platform -- exactly the special-casing the router refuses -- match the
    vendor's stated ceiling against the ones the config declares. An unmatched
    ceiling is still reported, under the header's own name.
    """
    gauges: Dict[str, Gauge] = {}
    for kind, candidates, fallback in (("requests", ("rpm", "rpd"), "requests"),
                                       ("tokens", ("tpm", "tpd"), "tokens")):
        limit = vendor.get(f"limit_{kind}")
        remaining = vendor.get(f"remaining_{kind}")
        if not isinstance(limit, int) or not isinstance(remaining, int):
            continue
        name = next((candidate for candidate in candidates
                     if limits.get(candidate) == limit), fallback)
        used = max(0, limit - remaining)
        gauges[name] = Gauge(name=name, used=used, limit=limit,
                             ratio=used / limit if limit else None,
                             source="vendor",
                             resets_in=vendor.get(f"reset_{kind}"))
    return gauges


def _gauges_for(row: Row) -> List[Gauge]:
    """Local gauges, overridden by whatever the vendor was able to answer."""
    gauges = _local_gauges(row.limits, row.minute, row.day)
    if not row.vendor or not row.vendor.get("ok"):
        return gauges

    from_vendor = _vendor_gauges(row.limits, row.vendor)
    merged = [from_vendor.pop(gauge.name, gauge) for gauge in gauges]
    # Anything the vendor reported that maps to no declared limit still belongs
    # on the row -- it is the vendor's own account of a budget we did not know
    # about, which is worth more than our silence.
    return merged + list(from_vendor.values())


def build_report(calls: List[dict], pool: Optional[dict],
                 vendor: Optional[Dict[str, dict]] = None,
                 now: Optional[float] = None) -> Report:
    """Build the whole report.

    `pool` may be None -- the panel still reports what was consumed, it just has
    nothing to measure it against. `vendor` may be empty -- then every gauge is
    this router's own count, and says so.
    """
    now = time.time() if now is None else now
    vendor = vendor or {}
    members = {member["provider"]: member for member in (pool or {}).get("pool", [])}

    rows: Dict[str, Row] = {}

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
            vendor=vendor.get(provider),
        )
        return rows[provider]

    # Every configured member gets a row even having spent nothing: untouched
    # budget is exactly what someone deciding whether to start a long run wants
    # to see, and a panel listing only what had been used would hide it.
    for provider in members:
        row_for(provider)

    since: Optional[float] = None
    for call in calls:
        timestamp = call.get("ts") or 0
        if since is None or timestamp < since:
            since = timestamp
        row = row_for(call.get("provider", "unknown"), call)
        age = now - timestamp
        row.total.add(call)
        if age <= DAY_SECONDS:
            row.day.add(call)
        if age <= MINUTE_SECONDS:
            row.minute.add(call)
        if row.last_call is None or timestamp > row.last_call:
            row.last_call = timestamp

    accounts: Dict[str, AccountSummary] = {}
    for row in rows.values():
        row.gauges = _gauges_for(row)
        with_limits = [gauge for gauge in row.gauges if gauge.ratio is not None]
        row.tightest = max(with_limits, key=lambda gauge: gauge.ratio, default=None)

        # Rolled up per account as well, because that is where a free tier's
        # real budget lives: Groq meters one org-wide request pool across every
        # model on the account, so the per-model rows flatter it
        # (docs/03-pool-model.md#34-priority-tiers).
        key = f"{row.platform}/{row.account}"
        summary = accounts.setdefault(key, AccountSummary(row.account, row.platform))
        summary.members += 1
        summary.minute.merge(row.minute)
        summary.day.merge(row.day)
        summary.total.merge(row.total)

    probed = max((reading.get("ts") or 0 for reading in vendor.values()), default=None)
    report = Report(
        generated=now,
        pool_generated=(pool or {}).get("generated"),
        config=(pool or {}).get("config"),
        since=since,
        calls=len(calls),
        probed=probed or None,
        rows=sorted(rows.values(), key=lambda row: (
            row.platform, row.account, row.priority if row.priority is not None else 999,
            row.model)),
        accounts=sorted(accounts.values(), key=lambda a: (a.platform, a.account)),
    )
    report.notes = _notes(report, pool, vendor, now)
    return report


def _notes(report: Report, pool: Optional[dict], vendor: dict, now: float) -> List[str]:
    notes = []
    if pool is None:
        notes.append("No pool snapshot found, so there are no limits to measure "
                     "against. Run `python -m llm_router` to write one.")
    if report.calls == 0:
        notes.append("The ledger is empty: nothing recorded since it was last cleared.")

    if not vendor:
        notes.append("No vendor reading yet: every figure below is this router's own "
                     "count, which misses anything else using the same keys. "
                     "`--probe` asks the vendors directly.")
    elif report.probed and now - report.probed > 3600:
        age = (now - report.probed) / 3600
        notes.append(f"The vendor reading is {age:.1f}h old; anything spent since is "
                     "counted locally or not at all. Re-run with `--probe`.")

    silent = sorted({row.platform for row in report.rows
                     if row.vendor and not row.vendor.get("reports", True)})
    if silent:
        notes.append(f"{', '.join(silent)} publishes no usage figures, so those rows "
                     "are this router's own count only.")

    failed = [row.provider for row in report.rows
              if row.vendor and row.vendor.get("reports") and not row.vendor.get("ok")]
    if failed:
        notes.append(f"{len(failed)} member(s) failed to answer the last probe "
                     f"({', '.join(failed)}).")

    orphans = [row.provider for row in report.rows if not row.configured]
    if orphans:
        notes.append(f"{len(orphans)} member(s) in the ledger are no longer in the pool "
                     f"({', '.join(orphans)}); their limits are unknown.")

    unbounded = [row for row in report.rows if row.configured and row.tightest is None]
    if unbounded:
        notes.append(f"{len(unbounded)} configured member(s) declare no limits in "
                     "config.yaml, so their consumption is reported without a ceiling.")
    return notes
