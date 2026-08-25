/**
 * The panel: one self-contained HTML file, written on demand.
 *
 * A snapshot rather than a served page, deliberately. Nothing here needs to be
 * live -- the question is "can I start a six-hour run on what's left", asked
 * once -- and a file has no port to collide with, no process to leave running
 * on a machine that is meant to be running agents unattended, and can be
 * committed next to a run's results when a session is worth explaining later.
 */

import type { Gauge, Report, Row } from "./report.ts";
import { tokens } from "./report.ts";
import { ago, compact, iso, percent } from "./format.ts";

function escape(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Green / amber / red, on the same thresholds the terminal table uses. */
function band(ratio: number): string {
  if (ratio >= 0.85) return "hot";
  if (ratio >= 0.6) return "warm";
  return "cool";
}

function gaugeCell(gauge: Gauge | undefined): string {
  if (!gauge) return `<td class="gauge empty">&mdash;</td>`;
  // Token gauges run to six figures; request gauges are small integers and read
  // worse rounded ("1.0K" for 1000 requests against a 1000/day ceiling).
  const scale = (value: number) =>
    (gauge.name.startsWith("t") ? compact(value) : String(value));
  if (gauge.limit === null || gauge.ratio === null) {
    // Consumption with no ceiling to draw a bar against -- still worth showing,
    // or Gemma's token use would appear nowhere.
    return `<td class="gauge open"><div class="figures">${scale(gauge.used)}`
      + ` &middot; <em>no cap</em></div></td>`;
  }
  const width = Math.min(100, Math.round(gauge.ratio * 100));
  return `<td class="gauge ${band(gauge.ratio)}">`
    + `<div class="bar"><span style="width:${width}%"></span></div>`
    + `<div class="figures">${scale(gauge.used)} / ${scale(gauge.limit)}`
    + ` <em>${percent(gauge.ratio)}</em></div></td>`;
}

function rowHtml(row: Row, now: number): string {
  const byName = new Map(row.gauges.map((gauge) => [gauge.name, gauge]));
  const state = row.configured ? "" : ` <span class="tag">retired from config</span>`;
  return `<tr>
    <td class="model"><code>${escape(row.model)}</code>${state}
      <div class="sub">${escape(row.provider)}${row.priority === null ? "" : ` &middot; priority ${row.priority}`}</div></td>
    ${gaugeCell(byName.get("rpm"))}
    ${gaugeCell(byName.get("tpm"))}
    ${gaugeCell(byName.get("rpd"))}
    ${gaugeCell(byName.get("tpd"))}
    <td class="num ${row.day.rateLimited > 0 ? "warn" : ""}">${row.day.rateLimited}</td>
    <td class="num ${row.day.errors > 0 ? "warn" : ""}">${row.day.errors}</td>
    <td class="when">${row.lastCall === null ? "never" : escape(ago(row.lastCall, now))}</td>
  </tr>`;
}

export function renderPanel(report: Report): string {
  const now = report.generated;
  const byAccount = new Map<string, Row[]>();
  for (const row of report.rows) {
    const key = `${row.platform}/${row.account}`;
    byAccount.set(key, [...(byAccount.get(key) ?? []), row]);
  }

  const sections = report.accounts.map((account) => {
    const key = `${account.platform}/${account.account}`;
    const rows = byAccount.get(key) ?? [];
    return `<section>
      <h2>${escape(account.account)} <span class="platform">${escape(account.platform)}</span></h2>
      <p class="summary">Last 24h across ${account.members} pool member(s):
        <strong>${account.day.requests}</strong> requests &middot;
        <strong>${compact(tokens(account.day))}</strong> tokens &middot;
        <strong>${account.day.rateLimited}</strong> rate-limited &middot;
        <strong>${account.day.errors}</strong> errored.
        Since the ledger began: ${account.total.requests} requests,
        ${compact(tokens(account.total))} tokens.</p>
      <table>
        <thead><tr>
          <th>Model</th>
          <th>RPM <span class="unit">60s</span></th><th>TPM <span class="unit">60s</span></th>
          <th>RPD <span class="unit">24h</span></th><th>TPD <span class="unit">24h</span></th>
          <th class="num">429</th><th class="num">Err</th><th>Last call</th>
        </tr></thead>
        <tbody>${rows.map((row) => rowHtml(row, now)).join("\n")}</tbody>
      </table>
    </section>`;
  }).join("\n");

  const notes = report.notes.length === 0 ? "" :
    `<ul class="notes">${report.notes.map((note) => `<li>${escape(note)}</li>`).join("")}</ul>`;

  return `<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pool quota &mdash; free_coding_agent</title>
<style>
:root { color-scheme: light dark;
  --bg: #fbfbfa; --fg: #1d1d1b; --dim: #6b6b66; --line: #e3e3df; --card: #fff;
  --cool: #3f8f5a; --warm: #b8860b; --hot: #b4402f; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #16161a; --fg: #e8e8e4; --dim: #96968f; --line: #2c2c33; --card: #1d1d22;
    --cool: #5cb37a; --warm: #d7a83c; --hot: #dd6a56; } }
* { box-sizing: border-box; }
body { margin: 0; padding: 2rem 1.25rem 4rem; background: var(--bg); color: var(--fg);
  font: 15px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1080px; margin: 0 auto; }
h1 { font-size: 1.4rem; margin: 0 0 .25rem; }
h2 { font-size: 1.05rem; margin: 0 0 .35rem; }
.meta, .summary, .sub, .when, footer { color: var(--dim); }
.meta { margin: 0 0 1.5rem; font-size: .85rem; }
.platform { font-weight: 400; color: var(--dim); font-size: .8rem;
  border: 1px solid var(--line); border-radius: 999px; padding: .05rem .5rem; margin-left: .35rem; }
.summary { font-size: .85rem; margin: 0 0 .75rem; }
.summary strong { color: var(--fg); }
section { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 1.1rem 1.15rem; margin-bottom: 1.25rem; overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: .82rem; }
th { text-align: left; font-weight: 600; color: var(--dim); padding: .3rem .5rem;
  border-bottom: 1px solid var(--line); white-space: nowrap; }
td { padding: .45rem .5rem; border-bottom: 1px solid var(--line); vertical-align: middle; }
tr:last-child td { border-bottom: none; }
code { font: 12.5px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.model { min-width: 15rem; }
.sub { font-size: .72rem; margin-top: .1rem; }
.tag { font-size: .68rem; border: 1px solid var(--hot); color: var(--hot);
  border-radius: 4px; padding: 0 .3rem; }
.num, .gauge .figures { text-align: right; font-variant-numeric: tabular-nums; }
.gauge { min-width: 6.5rem; }
.gauge .figures { font-size: .72rem; color: var(--dim); margin-top: .15rem; white-space: nowrap; }
.gauge em { font-style: normal; color: var(--fg); margin-left: .2rem; }
.gauge.empty { text-align: center; color: var(--dim); }
.gauge.open .figures { margin-top: 0; }
.unit { font-weight: 400; opacity: .65; }
.bar { background: var(--line); border-radius: 3px; height: 6px; overflow: hidden; }
.bar span { display: block; height: 100%; background: var(--cool); }
.warm .bar span { background: var(--warm); } .hot .bar span { background: var(--hot); }
.warn { color: var(--hot); }
.notes { background: var(--card); border: 1px solid var(--line); border-left: 3px solid var(--warm);
  border-radius: 6px; padding: .75rem 1rem .75rem 2rem; font-size: .82rem; margin: 0 0 1.25rem; }
footer { font-size: .78rem; margin-top: 1.5rem; }
</style>
</head><body><main>
<h1>Pool quota</h1>
<p class="meta">Snapshot taken ${escape(iso(now))} &middot;
  ${report.calls} call(s) on record${report.since === null ? "" : `, oldest ${escape(iso(report.since))}`}
  ${report.config === null ? "" : `&middot; pool from <code>${escape(report.config)}</code>`}</p>
${notes}
${sections}
<footer>Counted from this machine's ledger, not from the vendors: RPM/TPM are a
rolling 60&nbsp;seconds and RPD/TPD a rolling 24&nbsp;hours, so just after a
vendor's own reset this reads higher than the vendor does &mdash; it will not
show headroom that isn't there. Regenerate with
<code>node llm_router/quota/src/cli.ts panel</code>.</footer>
</main></body></html>
`;
}
