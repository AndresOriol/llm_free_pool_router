"""The panel: one self-contained HTML file, written on demand.

A snapshot rather than a served page, deliberately. Nothing here needs to be
live -- the question is "can I start a six-hour run on what's left", asked once
-- and a file has no port to collide with, no process to leave running on a
machine that is meant to be running agents unattended, and can be kept next to a
run's results when a session is worth explaining later.

Interactive is not the same as live: the filters are a few lines of inline
script over markup that is already complete, so the file still works from
`file://` with no server and no network. They earn their place because the pool
fans every model across every account on its platform -- a second Gemini key
doubles the rows -- and picking one account is the difference between reading
the page and scanning it.
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
.meta { margin: 0 0 1rem; font-size: .85rem; }
.filters { display: flex; flex-wrap: wrap; gap: .4rem; align-items: center;
  margin: 0 0 1.25rem; font-size: .8rem; }
.filters button { font: inherit; color: var(--dim); background: var(--card); cursor: pointer;
  border: 1px solid var(--line); border-radius: 999px; padding: .2rem .7rem; }
.filters button:hover { color: var(--fg); }
.filters button[aria-pressed="true"] { color: var(--bg); background: var(--fg);
  border-color: var(--fg); }
.filters label { margin-left: auto; color: var(--dim); display: flex; gap: .35rem;
  align-items: center; cursor: pointer; }
.platform { font-weight: 400; color: var(--dim); font-size: .8rem;
  border: 1px solid var(--line); border-radius: 999px; padding: .05rem .5rem; margin-left: .35rem; }
.summary { font-size: .85rem; margin: 0 0 .2rem; }
.summary strong { color: var(--fg); }
.source { font-size: .78rem; margin: 0 0 .75rem; }
section { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 1.1rem 1.15rem; margin-bottom: 1.25rem; overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: .82rem; }
th { text-align: left; font-weight: 600; color: var(--dim); padding: .3rem .5rem;
  border-bottom: 1px solid var(--line); white-space: nowrap; }
td { padding: .45rem .5rem; border-bottom: 1px solid var(--line); vertical-align: middle; }
tr:last-child td { border-bottom: none; }
code { font: 12.5px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.model { min-width: 14rem; }
.sub { font-size: .72rem; margin-top: .1rem; }
.tag { font-size: .68rem; border: 1px solid var(--hot); color: var(--hot);
  border-radius: 4px; padding: 0 .3rem; margin-left: .3rem; white-space: nowrap; }
.num, .gauge .figures { text-align: right; font-variant-numeric: tabular-nums; }
.gauge { min-width: 6.8rem; }
.gauge .figures { font-size: .72rem; color: var(--dim); margin-top: .15rem; white-space: nowrap; }
.gauge em { font-style: normal; color: var(--fg); margin-left: .2rem; }
.gauge.empty { text-align: center; color: var(--dim); }
.gauge.open .figures { margin-top: 0; }
.note { display: block; font-size: .66rem; color: var(--dim); }
.note.refused { color: var(--hot); }
.unit { font-weight: 400; opacity: .65; }
.bar { background: var(--line); border-radius: 3px; height: 6px; overflow: hidden; }
.bar span { display: block; height: 100%; background: var(--cool); }
.warm .bar span { background: var(--warm); } .hot .bar span { background: var(--hot); }
.warn { color: var(--hot); }
.notes { background: var(--card); border: 1px solid var(--line); border-left: 3px solid var(--warm);
  border-radius: 6px; padding: .75rem 1rem .75rem 2rem; font-size: .82rem; margin: 0 0 1.25rem; }
footer { font-size: .78rem; margin-top: 1.5rem; }
"""

# The markup is complete before this runs: filtering only sets `hidden`, so the
# page stays readable with scripting off. Wrapped in a function because a page
# someone may paste into a console or another document should not be leaving
# `sections` and `buttons` lying around in the global scope.
_SCRIPT = """
(() => {
const sections = [...document.querySelectorAll('section[data-account]')];
const buttons = [...document.querySelectorAll('.filters button')];
const idleOnly = document.getElementById('hide-idle');
let account = 'all';

function apply() {
  for (const section of sections) {
    section.hidden = account !== 'all' && section.dataset.account !== account;
    for (const row of section.querySelectorAll('tr[data-used]')) {
      row.hidden = idleOnly.checked && row.dataset.used === '0';
    }
  }
  for (const button of buttons) {
    button.setAttribute('aria-pressed', String(button.dataset.account === account));
  }
}

for (const button of buttons) {
  button.addEventListener('click', () => { account = button.dataset.account; apply(); });
}
idleOnly.addEventListener('change', apply);
apply();
})();
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
    # A refusal is inside `used` -- it spent a request -- but saying so is the
    # difference between "we sent 30" and "we sent 20 and were turned away ten
    # times", which are the same number and completely different situations.
    refused = ("" if not gauge.refused
               else f'<span class="note refused">{gauge.refused} refused</span>')
    # Only a window with something in it has a reset. An empty one has not
    # started: it will, whenever the next attempt is made.
    resets = ("" if not gauge.used or gauge.resets_in is None
              else f'<span class="note">resets in {duration(gauge.resets_in)}</span>')

    if gauge.limit is None or gauge.ratio is None:
        return ('<td class="gauge open"><div class="figures">'
                f'{figure} &middot; <em>no cap</em>{refused}{resets}</div></td>')

    width = min(100, round(gauge.ratio * 100))
    return (f'<td class="gauge {_band(gauge.ratio)}">'
            f'<div class="bar"><span style="width:{width}%"></span></div>'
            f'<div class="figures">{figure} / {scaled(gauge.name, gauge.limit)}'
            f'<em>{percent(gauge.ratio)}</em>{refused}{resets}</div></td>')


def _row_html(row: Row, now: float) -> str:
    by_name = {gauge.name: gauge for gauge in row.gauges}
    tags = "" if row.configured else '<span class="tag">retired from config</span>'
    if row.blocked_for is not None:
        # The provider's own Retry-After, which outranks any arithmetic of ours.
        tags += f'<span class="tag">blocked {duration(row.blocked_for)}</span>'
    priority = "" if row.priority is None else f" &middot; priority {row.priority}"
    cells = "".join(_gauge_cell(by_name.get(name))
                    for name in ("rpm", "tpm", "rpd", "tpd"))
    return f"""<tr data-used="{1 if row.day.requests else 0}">
    <td class="model"><code>{escape(row.model)}</code>{tags}
      <div class="sub">{escape(row.account)}{priority}</div></td>
    {cells}
    <td class="num {'warn' if row.day.rate_limited else ''}">{row.day.rate_limited}</td>
    <td class="num {'warn' if row.day.errors else ''}">{row.day.errors}</td>
    <td class="when">{'never' if row.last_call is None else escape(ago(row.last_call, now))}</td>
  </tr>"""


def _section(account: AccountSummary, rows: List[Row], now: float) -> str:
    body = "\n".join(_row_html(row, now) for row in rows)
    blocked = ("" if account.blocked_for is None else
               f" A member here is blocked for another {duration(account.blocked_for)}.")
    return f"""<section data-account="{escape(account.account)}">
      <h2>{escape(account.account)} <span class="platform">{escape(account.platform)}</span></h2>
      <p class="summary">Last 24h across {account.members} pool member(s) on this
        account: <strong>{account.day.requests}</strong> requests &middot;
        <strong>{compact(account.day.tokens)}</strong> tokens &middot;
        <strong>{account.day.rate_limited}</strong> refused &middot;
        <strong>{account.day.errors}</strong> errored.</p>
      <p class="source">Every budget below belongs to this account alone &mdash; another
        account on the same platform shares none of it, not even for the same
        model.{blocked}</p>
      <table>
        <thead><tr>
          <th>Model</th>
          <th>RPM <span class="unit">60s</span></th><th>TPM <span class="unit">60s</span></th>
          <th>RPD <span class="unit">24h</span></th><th>TPD <span class="unit">24h</span></th>
          <th class="num">Refused</th><th class="num">Err</th><th>Last call</th>
        </tr></thead>
        <tbody>{body}</tbody>
      </table>
    </section>"""


def _filters(report: Report) -> str:
    buttons = ['<button type="button" data-account="all" aria-pressed="true">'
               f'All accounts <span class="unit">{len(report.accounts)}</span></button>']
    for summary in report.accounts:
        buttons.append(f'<button type="button" data-account="{escape(summary.account)}" '
                       f'aria-pressed="false">{escape(summary.account)} '
                       f'<span class="unit">{escape(summary.platform)}</span></button>')
    return (f'<div class="filters">{"".join(buttons)}'
            '<label><input type="checkbox" id="hide-idle"> only members used in the '
            'last 24h</label></div>')


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
  {report.calls} call(s) on record{oldest} {config}</p>
{_filters(report)}
{notes}
{sections}
<footer>Counted from this router's own ledger, so anything else using the same
keys is invisible here. A window opens with its first attempt and resets one
length later, which reads pessimistically against a vendor that refills
continuously &mdash; it will not show headroom that isn't there. A refused
attempt counts as a request, because it spent one, and as no tokens, because it
spent none. Regenerate with
<code>python -m llm_router.quota panel</code>.</footer>
</main>
<script>{_SCRIPT}</script>
</body></html>
"""
