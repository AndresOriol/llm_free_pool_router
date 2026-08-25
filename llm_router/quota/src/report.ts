/**
 * Turning a list of attempts into the question a human actually asks: how close
 * is each free account to the wall, right now and today.
 *
 * **The windows here are rolling, and the vendors' are not.** Gemini resets its
 * daily quota on its own clock (midnight Pacific); Groq's day starts whenever
 * Groq says. Tracking each vendor's reset boundary would mean encoding a
 * timezone and a policy per platform, and getting one wrong silently. A rolling
 * 60s / 24h window needs neither: just after a vendor's reset it still counts
 * calls the vendor has already forgiven, so it errs toward showing *more*
 * consumption than the vendor sees. It will never claim headroom that isn't
 * there, which is the only direction that matters to an unattended run.
 */

import type { Call, Limits, PoolMember, PoolSnapshot } from "./ledger.ts";

export const MINUTE_SECONDS = 60;
export const DAY_SECONDS = 86_400;

export interface Usage {
  requests: number;
  tokensIn: number;
  tokensOut: number;
  rateLimited: number;
  errors: number;
}

/** One metered quantity, and the ceiling it is spending against if there is one. */
export interface Gauge {
  name: keyof Limits;
  used: number;
  /** Null when the vendor declares no ceiling (Gemma's TPM). */
  limit: number | null;
  /** 0..n -- above 1 when the rolling window has outrun the vendor's. Null with no limit. */
  ratio: number | null;
}

export interface Row {
  provider: string;
  account: string;
  platform: string;
  model: string;
  priority: number | null;
  limits: Limits;
  minute: Usage;
  day: Usage;
  total: Usage;
  /** All four metered quantities, always, in RPM/TPM/RPD/TPD order. */
  gauges: Gauge[];
  /** The gauge closest to its ceiling: what will stop this member first. */
  tightest: Gauge | null;
  lastCall: number | null;
  /** False when the ledger has calls for a member the config no longer has. */
  configured: boolean;
}

export interface AccountSummary {
  account: string;
  platform: string;
  members: number;
  minute: Usage;
  day: Usage;
  total: Usage;
}

export interface Report {
  generated: number;
  poolGenerated: number | null;
  config: string | null;
  /** Timestamp of the oldest call on record, or null on an empty ledger. */
  since: number | null;
  calls: number;
  rows: Row[];
  accounts: AccountSummary[];
  /** What a reader should know before believing the numbers. */
  notes: string[];
}

function emptyUsage(): Usage {
  return { requests: 0, tokensIn: 0, tokensOut: 0, rateLimited: 0, errors: 0 };
}

function add(usage: Usage, call: Call): void {
  usage.requests += 1;
  usage.tokensIn += call.tokens_in ?? 0;
  usage.tokensOut += call.tokens_out ?? 0;
  if (call.outcome === "rate_limited") usage.rateLimited += 1;
  else if (call.outcome === "error") usage.errors += 1;
}

function merge(into: Usage, from: Usage): void {
  into.requests += from.requests;
  into.tokensIn += from.tokensIn;
  into.tokensOut += from.tokensOut;
  into.rateLimited += from.rateLimited;
  into.errors += from.errors;
}

/** Prompt and reply together: every free tier meters both against one budget. */
export function tokens(usage: Usage): number {
  return usage.tokensIn + usage.tokensOut;
}

/**
 * A gauge per metered quantity, whether or not a ceiling is declared for it.
 * An undeclared limit still has consumption worth showing -- dropping the gauge
 * would leave Gemma's token use nowhere on the page.
 */
function gaugesFor(limits: Limits, minute: Usage, day: Usage): Gauge[] {
  const metered: Array<[keyof Limits, number | undefined, number]> = [
    ["rpm", limits.rpm, minute.requests],
    ["tpm", limits.tpm, tokens(minute)],
    ["rpd", limits.rpd, day.requests],
    ["tpd", limits.tpd, tokens(day)],
  ];
  return metered.map(([name, declared, used]) => {
    const limit = typeof declared === "number" && declared > 0 ? declared : null;
    return { name, used, limit, ratio: limit === null ? null : used / limit };
  });
}

/**
 * Build the whole report. `pool` may be null -- the panel still reports what was
 * consumed, it just has nothing to measure it against.
 */
export function buildReport(calls: Call[], pool: PoolSnapshot | null,
                            now: number = Date.now() / 1000): Report {
  const members = new Map<string, PoolMember>();
  for (const member of pool?.pool ?? []) members.set(member.provider, member);

  const rows = new Map<string, Row>();
  const rowFor = (provider: string, call?: Call): Row => {
    const existing = rows.get(provider);
    if (existing) return existing;
    const member = members.get(provider);
    const row: Row = {
      provider,
      account: member?.account ?? call?.account ?? "unknown",
      platform: member?.platform ?? call?.platform ?? "unknown",
      model: member?.model ?? call?.model ?? "unknown",
      priority: member?.priority ?? null,
      limits: member?.limits ?? {},
      minute: emptyUsage(),
      day: emptyUsage(),
      total: emptyUsage(),
      gauges: [],
      tightest: null,
      lastCall: null,
      configured: member !== undefined,
    };
    rows.set(provider, row);
    return row;
  };

  // Every configured member gets a row even having spent nothing: untouched
  // budget is exactly what someone deciding whether to start a long run wants
  // to see, and a panel listing only what had been used would hide it.
  for (const member of members.values()) rowFor(member.provider);

  let since: number | null = null;
  for (const call of calls) {
    if (since === null || call.ts < since) since = call.ts;
    const row = rowFor(call.provider, call);
    const age = now - call.ts;
    add(row.total, call);
    if (age <= DAY_SECONDS) add(row.day, call);
    if (age <= MINUTE_SECONDS) add(row.minute, call);
    if (row.lastCall === null || call.ts > row.lastCall) row.lastCall = call.ts;
  }

  const accounts = new Map<string, AccountSummary>();
  for (const row of rows.values()) {
    row.gauges = gaugesFor(row.limits, row.minute, row.day);
    row.tightest = row.gauges
      .filter((gauge) => gauge.ratio !== null)
      .reduce<Gauge | null>(
        (worst, gauge) => (worst === null || gauge.ratio! > worst.ratio! ? gauge : worst),
        null);

    // Rolled up per account as well, because that is where a free tier's real
    // budget lives: Groq meters one org-wide request pool across every model on
    // the account, so the per-model rows above flatter it
    // (docs/03-pool-model.md#34-priority-tiers).
    const key = `${row.platform}/${row.account}`;
    const summary = accounts.get(key) ?? {
      account: row.account,
      platform: row.platform,
      members: 0,
      minute: emptyUsage(),
      day: emptyUsage(),
      total: emptyUsage(),
    };
    summary.members += 1;
    merge(summary.minute, row.minute);
    merge(summary.day, row.day);
    merge(summary.total, row.total);
    accounts.set(key, summary);
  }

  const notes: string[] = [];
  if (pool === null) {
    notes.push("No pool snapshot found, so there are no limits to measure against. "
      + "Run `python -m llm_router` to write one.");
  }
  if (calls.length === 0) {
    notes.push("The ledger is empty: nothing recorded since it was last cleared.");
  }
  const orphans = [...rows.values()].filter((row) => !row.configured);
  if (orphans.length > 0) {
    notes.push(`${orphans.length} member(s) in the ledger are no longer in the pool `
      + `(${orphans.map((row) => row.provider).join(", ")}); their limits are unknown.`);
  }
  const unbounded = [...rows.values()]
    .filter((row) => row.configured && row.tightest === null);
  if (unbounded.length > 0) {
    notes.push(`${unbounded.length} configured member(s) declare no limits in `
      + "config.yaml, so their consumption is reported without a ceiling.");
  }

  const ordered = [...rows.values()].sort((a, b) =>
    a.platform.localeCompare(b.platform)
    || a.account.localeCompare(b.account)
    || (a.priority ?? 999) - (b.priority ?? 999)
    || a.model.localeCompare(b.model));

  return {
    generated: now,
    poolGenerated: pool?.generated ?? null,
    config: pool?.config ?? null,
    since,
    calls: calls.length,
    rows: ordered,
    accounts: [...accounts.values()].sort((a, b) =>
      a.platform.localeCompare(b.platform) || a.account.localeCompare(b.account)),
    notes,
  };
}
