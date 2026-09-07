"""The panel: one self-contained HTML file, written on demand.

A snapshot rather than a served page. The question -- can I start a six-hour run
on what is left -- is asked once, and a file has no port to collide with and no
process to leave running on a machine meant to be running agents.

One section per platform, and inside it one table that holds both views: a model
summed over every account serving it (the default, and the reason this exists),
and the same models per account (the drill-down). The controls sit in the
section they act on, because a filter three headings away from its table is a
filter people forget is on.

Interactive is not live: both views are written into the file, and the script
only hides rows and reorders them. With scripting off the page still reads.

Prose is kept out of the tables deliberately -- the caveats are real but they
are footnotes, not a preamble. They live at the bottom, in one voice.
"""

from html import escape
from typing import List, Optional

from .format import ago, compact, duration, percent, scaled, stamp
from .report import Gauge, Report, Row, declared_limits

_STYLE = """
:root { color-scheme: light dark;
  --bg: #fbfbfa; --fg: #1d1d1b; --dim: #6b6b66; --line: #e3e3df; --card: #fff;
  --cool: #3f8f5a; --warm: #b8860b; --hot: #b4402f; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #16161a; --fg: #e8e8e4; --dim: #96968f; --line: #2c2c33; --card: #1d1d22;
    --cool: #5cb37a; --warm: #d7a83c; --hot: #dd6a56; } }
* { box-sizing: border-box; }
body { margin: 0; padding: 2rem 1.25rem 3rem; background: var(--bg); color: var(--fg);
  font: 15px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1120px; margin: 0 auto; }
h1 { font-size: 1.3rem; margin: 0 0 .2rem; }
h2 { font-size: 1rem; margin: 0; }
.meta, .sub, .when, .badge, footer { color: var(--dim); }
.meta { margin: 0 0 1.5rem; font-size: .8rem; }
section { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: .9rem 1rem 1rem; margin-bottom: 1rem; overflow-x: auto; }
.head { display: flex; flex-wrap: wrap; gap: .5rem; align-items: baseline;
  margin-bottom: .6rem; }
.badge { font-size: .78rem; }
.badge b { color: var(--fg); font-weight: 600; }
.controls { display: flex; flex-wrap: wrap; gap: .35rem; align-items: center;
  margin-bottom: .6rem; font-size: .78rem; }
.controls button { font: inherit; color: var(--dim); background: transparent; cursor: pointer;
  border: 1px solid var(--line); border-radius: 999px; padding: .15rem .6rem; }
.controls button:hover { color: var(--fg); }
.controls button[aria-pressed="true"] { color: var(--bg); background: var(--fg);
  border-color: var(--fg); }
.controls label { display: flex; gap: .3rem; align-items: center; cursor: pointer;
  color: var(--dim); margin-left: .3rem; }
.controls input[type="search"] { font: inherit; color: var(--fg); background: transparent;
  border: 1px solid var(--line); border-radius: 999px; padding: .15rem .6rem;
  margin-left: auto; min-width: 9rem; }
table { border-collapse: collapse; width: 100%; font-size: .8rem; }
th { text-align: right; font-weight: 600; color: var(--dim); padding: .25rem .45rem;
  border-bottom: 1px solid var(--line); white-space: nowrap; cursor: pointer;
  user-select: none; }
th:first-child { text-align: left; }
th[aria-sort]::after { content: " \\2191"; opacity: .9; }
th[aria-sort="descending"]::after { content: " \\2193"; }
td { padding: .4rem .45rem; border-bottom: 1px solid var(--line); vertical-align: middle; }
tr:last-child td { border-bottom: none; }
code { font: 12.5px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.model { min-width: 13rem; }
.sub { font-size: .7rem; margin-top: .05rem; }
.tag { font-size: .66rem; border: 1px solid var(--line); color: var(--dim);
  border-radius: 4px; padding: 0 .3rem; margin-left: .3rem; white-space: nowrap; }
.num, .gauge .figures { text-align: right; font-variant-numeric: tabular-nums; }
.gauge { min-width: 6.5rem; }
.gauge .figures { font-size: .7rem; color: var(--dim); margin-top: .12rem; white-space: nowrap; }
.gauge em { font-style: normal; color: var(--fg); margin-left: .2rem; }
.gauge.empty, .num.zero { color: var(--dim); }
.note { display: block; font-size: .64rem; color: var(--dim); }
.parts { opacity: .7; }
.bar { background: var(--line); border-radius: 3px; height: 5px; overflow: hidden; }
.bar span { display: block; height: 100%; background: var(--cool); }
.warm .bar span { background: var(--warm); } .hot .bar span { background: var(--hot); }
footer { font-size: .75rem; margin-top: 1.25rem; }
footer p { margin: .35rem 0; }
"""

# One controller per section, so a platform's filters cannot reach across into
# another's. Sorting reorders every row in the table, visible or not, which
# keeps the order stable when the view is switched.
_SCRIPT = """
for (const section of document.querySelectorAll('section[data-platform]')) {
  const rows = [...section.querySelectorAll('tbody tr')];
  const buttons = [...section.querySelectorAll('.controls button')];
  const usedOnly = section.querySelector('.used-only');
  const search = section.querySelector('input[type="search"]');
  const headers = [...section.querySelectorAll('th')];
  const body = section.querySelector('tbody');
  let scope = 'models';
  let sort = null;

  const key = (row, column) => {
    const cell = row.children[column];
    const raw = cell.dataset.sort;
    const number = parseFloat(raw);
    return Number.isNaN(number) ? raw.toLowerCase() : number;
  };

  function apply() {
    const query = search.value.trim().toLowerCase();
    for (const row of rows) {
      const inScope = scope === 'models'
        ? row.dataset.scope === 'models'
        : row.dataset.account === scope;
      const used = !usedOnly.checked || row.dataset.used === '1';
      const found = !query || row.dataset.name.includes(query);
      row.hidden = !(inScope && used && found);
    }
    for (const button of buttons) {
      button.setAttribute('aria-pressed', String((button.dataset.account || 'models') === scope));
    }
    if (sort) {
      const ordered = [...rows].sort((a, b) => {
        const left = key(a, sort.column), right = key(b, sort.column);
        if (left === right) return 0;
        return (left < right ? -1 : 1) * (sort.direction === 'ascending' ? 1 : -1);
      });
      for (const row of ordered) body.appendChild(row);
    }
    headers.forEach((header, index) => {
      if (sort && sort.column === index) header.setAttribute('aria-sort', sort.direction);
      else header.removeAttribute('aria-sort');
    });
  }

  buttons.forEach((button) => button.addEventListener('click', () => {
    scope = button.dataset.account || 'models';
    apply();
  }));
  headers.forEach((header, index) => header.addEventListener('click', () => {
    const descending = sort && sort.column === index && sort.direction === 'ascending';
    sort = { column: index, direction: descending ? 'descending' : 'ascending' };
    apply();
  }));
  usedOnly.addEventListener('change', apply);
  search.addEventListener('input', apply);
  apply();
}
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
        return '<td class="gauge empty" data-sort="-1">&mdash;</td>'

    figure = scaled(gauge.name, gauge.used)
    # Only a window with something in it has a reset: an empty one has not
    # started, and will whenever the next attempt is made.
    resets = ("" if not gauge.used or gauge.resets_in is None
              else f'<span class="note">resets in {duration(gauge.resets_in)}</span>')

    if gauge.limit is None or gauge.ratio is None:
        return ('<td class="gauge empty" data-sort="-1"><div class="figures">'
                f'{figure} &middot; no cap{resets}</div></td>')

    # A summed ceiling is shown as the sum it is: 2 x 5, not a mystery 10.
    parts = ("" if gauge.sources < 2
             else f'<span class="parts"> ({gauge.sources}&times;'
                  f'{scaled(gauge.name, gauge.limit // gauge.sources)})</span>')
    width = min(100, round(gauge.ratio * 100))
    return (f'<td class="gauge {_band(gauge.ratio)}" data-sort="{gauge.ratio:.6f}">'
            f'<div class="bar"><span style="width:{width}%"></span></div>'
            f'<div class="figures">{figure} / {scaled(gauge.name, gauge.limit)}{parts}'
            f'<em>{percent(gauge.ratio)}</em>{resets}</div></td>')


def _count_cell(value: int) -> str:
    return f'<td class="num{"" if value else " zero"}" data-sort="{value}">{value}</td>'


def _entry_row(entry, scope: str, subtitle: str, now: float, names: tuple,
               account: str = "", retired: bool = False) -> str:
    """One table row, over a model summary or a single pool member alike."""
    by_name = {gauge.name: gauge for gauge in entry.gauges}
    tags = '<span class="tag">retired</span>' if retired else ""
    if entry.blocked_for is not None:
        # The provider's own Retry-After, which outranks any arithmetic of ours.
        tags += f'<span class="tag">blocked {duration(entry.blocked_for)}</span>'
    window = entry.max_input_tokens
    return f"""<tr data-scope="{scope}" data-account="{escape(account)}"
      data-used="{1 if entry.day.requests else 0}" data-name="{escape(entry.model.lower())}">
    <td class="model" data-sort="{escape(entry.model.lower())}">
      <code>{escape(entry.model)}</code>{tags}
      <div class="sub">{subtitle}</div></td>
    <td class="num" data-sort="{window or 0}">{compact(window) if window else "&mdash;"}</td>
    {"".join(_gauge_cell(by_name.get(name)) for name in names)}
    {_count_cell(entry.day.rate_limited)}{_count_cell(entry.day.errors)}
    <td class="when" data-sort="{entry.last_call or 0}">{
      'never' if entry.last_call is None else escape(ago(entry.last_call, now))}</td>
  </tr>"""


def _controls(accounts: List[str]) -> str:
    buttons = ['<button type="button" aria-pressed="true">All accounts</button>']
    buttons += [f'<button type="button" data-account="{escape(account)}" '
                f'aria-pressed="false">{escape(account)}</button>' for account in accounts]
    return (f'<div class="controls">{"".join(buttons)}'
            '<label><input type="checkbox" class="used-only"> used today</label>'
            '<input type="search" placeholder="filter models"></div>')


def _section(platform, models, rows: List[Row], now: float) -> str:
    # A quantity nobody on this platform has a ceiling for gets no column.
    names = declared_limits(models + rows)
    head = "".join(f"<th>{label}</th>" for label in
                   ("Model", "Context", *(name.upper() for name in names),
                    "Refused", "Err", "Last"))
    body = [_entry_row(model, "models",
                       f'{len(model.accounts)} account(s)'
                       + ("" if model.priority is None else f' &middot; priority {model.priority}'),
                       now, names)
            for model in models]
    body += [_entry_row(row, "account", escape(row.account), now, names,
                        account=row.account, retired=not row.configured)
             for row in rows]
    return f"""<section data-platform="{escape(platform.platform)}">
      <div class="head"><h2>{escape(platform.platform)}</h2>
        <span class="badge">{platform.models} models over
          {len(platform.accounts)} account(s) &middot; today
          <b>{platform.day.requests}</b> requests, <b>{compact(platform.day.tokens)}</b>
          tokens, <b>{platform.day.rate_limited}</b> refused</span></div>
      {_controls(platform.accounts)}
      <table><thead><tr>{head}</tr></thead>
      <tbody>{"".join(body)}</tbody></table>
    </section>"""


def render_panel(report: Report) -> str:
    now = report.generated
    sections = "".join(
        _section(platform,
                 [model for model in report.models
                  if model.platform == platform.platform],
                 [row for row in report.rows if row.platform == platform.platform],
                 now)
        for platform in report.platforms)

    footnotes = "".join(f"<p>{escape(note)}</p>" for note in report.notes)
    since = "" if report.since is None else f" since {escape(stamp(report.since))}"

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pool quota &mdash; free_coding_agent</title>
<style>{_STYLE}</style>
</head><body><main>
<h1>Pool quota</h1>
<p class="meta">{escape(stamp(now))} &middot; {report.calls} call(s) recorded{since}</p>
{sections}
<footer>
{footnotes}
<p>Counted from this router's ledger, so a key used elsewhere is under-counted
here. Windows are the vendor's own: the clock minute, and the day as it turns
where the vendor turns it &mdash; midnight Pacific for Gemini, midnight UTC for
Groq &mdash; so <em>resets in</em> is a fact about the calendar, not about when
we started. Gemini meters its tokens per minute over the prompt only, so replies
are not charged against it. A refused attempt counts as a request and as no
tokens; one that never got an answer counts as neither. Ceilings shown for several accounts are their ceilings added
together. Rebuild with <code>python -m llm_router.quota panel</code>.</p>
</footer>
</main>
<script>{_SCRIPT}</script>
</body></html>
"""
