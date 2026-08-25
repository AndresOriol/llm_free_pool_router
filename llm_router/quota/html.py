"""The panel: one self-contained HTML file, written on demand.

A snapshot rather than a served page, deliberately. Nothing here needs to be
live -- the question is "can I start a six-hour run on what's left", asked once
-- and a file has no port to collide with, no process to leave running on a
machine that is meant to be running agents unattended, and can be kept next to a
run's results when a session is worth explaining later.
"""

from html import escape
from typing import List, Optional

from .format import ago, compact, duration, iso, percent, scaled
from .report import AccountSummary, Gauge, Report, Row

_STYLE = """
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
.meta, .summary, .sub, .when, .source, footer { color: var(--dim); }
.meta { margin: 0 0 1.5rem; font-size: .85rem; }
.platform { font-weight: 400; color: var(--dim); font-size: .8rem;
  border: 1px solid var(--line); border-radius: 999px; padding: .05rem .5rem; margin-left: .35rem; }
.summary { font-size: .85rem; margin: 0 0 .2rem; }
.summary strong { color: var(--fg); }
.source { font-size: .78rem; margin: 0 0 .75rem; }
.source b { color: var(--fg); font-weight: 600; }
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
.mark { font-size: .6rem; letter-spacing: .02em; text-transform: uppercase;
  color: var(--dim); margin-left: .25rem; }
.resets { display: block; font-size: .66rem; color: var(--dim); }
.unit { font-weight: 400; opacity: .65; }
.bar { background: var(--line); border-radius: 3px; height: 6px; overflow: hidden; }
.bar span { display: block; height: 100%; background: var(--cool); }
.warm .bar span { background: var(--warm); } .hot .bar span { background: var(--hot); }
.warn { color: var(--hot); }
.notes { background: var(--card); border: 1px solid var(--line); border-left: 3px solid var(--warm);
  border-radius: 6px; padding: .75rem 1rem .75rem 2rem; font-size: .82rem; margin: 0 0 1.25rem; }
footer { font-size: .78rem; margin-top: 1.5rem; }
"""


def _band(ratio: float) -> str:
    """Green / amber / red, on the same thresholds the terminal table uses."""
    if ratio >= 0.85:
        return "hot"
    if ratio >= 0.6:
        return "warm"
    return "cool"


def _gauge_cell(gauge: Optional[Gauge]) -> str:
    if gauge is None:
        return '<td class="gauge empty">&mdash;</td>'

    figure = scaled(gauge.name, gauge.used)
    # Only the vendor's cells are marked. Marking the local ones instead was
    # tried and tags most of the table: Groq answers for two of the four
    # windows, so "unmarked" is the common case and has to be the quiet one.
    mark = '<span class="mark">vendor</span>' if gauge.source == "vendor" else ""
    if gauge.limit is None or gauge.ratio is None:
        return ('<td class="gauge open"><div class="figures">'
                f'{figure} &middot; <em>no cap</em>{mark}</div></td>')

    # A sub-second refill is the bucket being full, not news worth a line.
    resets = ("" if not gauge.resets_in or gauge.resets_in < 5
              else f'<span class="resets">refills in {duration(gauge.resets_in)}</span>')
    width = min(100, round(gauge.ratio * 100))
    return (f'<td class="gauge {_band(gauge.ratio)}">'
            f'<div class="bar"><span style="width:{width}%"></span></div>'
            f'<div class="figures">{figure} / {scaled(gauge.name, gauge.limit)}'
            f'<em>{percent(gauge.ratio)}</em>{mark}{resets}</div></td>')


def _row_html(row: Row, now: float) -> str:
    by_name = {gauge.name: gauge for gauge in row.gauges}
    state = "" if row.configured else ' <span class="tag">retired from config</span>'
    priority = "" if row.priority is None else f" &middot; priority {row.priority}"
    cells = "".join(_gauge_cell(by_name.get(name))
                    for name in ("rpm", "tpm", "rpd", "tpd"))
    extra = "".join(_gauge_cell(gauge) for gauge in row.gauges
                    if gauge.name not in ("rpm", "tpm", "rpd", "tpd"))
    return f"""<tr>
    <td class="model"><code>{escape(row.model)}</code>{state}
      <div class="sub">{escape(row.provider)}{priority}</div></td>
    {cells}{extra}
    <td class="num {'warn' if row.day.rate_limited else ''}">{row.day.rate_limited}</td>
    <td class="num {'warn' if row.day.errors else ''}">{row.day.errors}</td>
    <td class="when">{'never' if row.last_call is None else escape(ago(row.last_call, now))}</td>
  </tr>"""


def _source_line(rows: List[Row], now: float) -> str:
    """Where this section's figures came from, said once instead of per cell."""
    readings = [row.vendor for row in rows if row.vendor and row.vendor.get("ok")]
    if readings:
        read_at = max(reading.get("ts", 0) for reading in readings)
        return (f"Cells marked <b>vendor</b> are the account's own count, read "
                f"{escape(ago(read_at, now))} &mdash; they include every use of the "
                f"key, not just this router's. The rest is this router's ledger.")
    silent = any(row.vendor and not row.vendor.get("reports", True) for row in rows)
    why = ("this platform publishes no usage figures"
           if silent else "no vendor reading yet &mdash; run with <code>--probe</code>")
    return (f"Figures below are <b>this router's own count</b>: {why}. "
            "Anything else using the same key is invisible here.")


def _section(account: AccountSummary, rows: List[Row], now: float) -> str:
    body = "\n".join(_row_html(row, now) for row in rows)
    return f"""<section>
      <h2>{escape(account.account)} <span class="platform">{escape(account.platform)}</span></h2>
      <p class="summary">Seen by this router in the last 24h, across {account.members}
        pool member(s): <strong>{account.day.requests}</strong> requests &middot;
        <strong>{compact(account.day.tokens)}</strong> tokens &middot;
        <strong>{account.day.rate_limited}</strong> rate-limited &middot;
        <strong>{account.day.errors}</strong> errored.</p>
      <p class="source">{_source_line(rows, now)}</p>
      <table>
        <thead><tr>
          <th>Model</th>
          <th>RPM <span class="unit">60s</span></th><th>TPM <span class="unit">60s</span></th>
          <th>RPD <span class="unit">24h</span></th><th>TPD <span class="unit">24h</span></th>
          <th class="num">429</th><th class="num">Err</th><th>Last call</th>
        </tr></thead>
        <tbody>{body}</tbody>
      </table>
    </section>"""


def render_panel(report: Report) -> str:
    now = report.generated
    sections = "\n".join(
        _section(account,
                 [row for row in report.rows
                  if row.account == account.account and row.platform == account.platform],
                 now)
        for account in report.accounts)

    notes = ""
    if report.notes:
        items = "".join(f"<li>{escape(note)}</li>" for note in report.notes)
        notes = f'<ul class="notes">{items}</ul>'

    oldest = "" if report.since is None else f", oldest {escape(iso(report.since))}"
    config = "" if not report.config else f"&middot; pool from <code>{escape(report.config)}</code>"

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pool quota &mdash; free_coding_agent</title>
<style>{_STYLE}</style>
</head><body><main>
<h1>Pool quota</h1>
<p class="meta">Snapshot taken {escape(iso(now))} &middot;
  {report.calls} call(s) on this router's record{oldest} {config}</p>
{notes}
{sections}
<footer>A vendor figure is what the account itself reports and counts every use
of the key; a local figure is this router's own ledger, over a rolling
60&nbsp;seconds and 24&nbsp;hours, which reads high just after a vendor's reset
and never shows headroom that isn't there. Regenerate with
<code>python -m llm_router.quota panel</code>, or
<code>--probe</code> to ask the vendors again.</footer>
</main></body></html>
"""
