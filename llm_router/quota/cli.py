"""The command line over the ledger.

Two views of one report. By default a model is one line, summed over every
account that serves it -- the question is "how much Gemini is left", not "how
much is left on key two". `--account` is the drill-down: one account, member by
member. `status` prints a table for a human glancing at a terminal and, with `--json`,
the whole report as data -- both views at once, which is how a coding agent asks
how much of the free tier is left before starting something long. `panel` writes
the HTML snapshot, where the same drill-down is a button.

Nothing here touches the network or spends a request: every figure comes off
`ledger.jsonl`, so an agent may ask as often as it likes.
"""

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional

from .format import ago, compact, duration, percent, scaled, stamp
from .html import render_panel
from .ledger import read_ledger, read_pool, usage_dir
from .report import (Report, _fold_models, _fold_platforms, build_report,
                     declared_limits)


def _gauge_text(entry, name: str) -> str:
    gauge = next((candidate for candidate in entry.gauges if candidate.name == name), None)
    if gauge is None:
        return "-"
    if gauge.limit is None or gauge.ratio is None:
        return f"{scaled(name, gauge.used)} (no cap)"
    return f"{scaled(name, gauge.used)}/{scaled(name, gauge.limit)} {percent(gauge.ratio)}"


def _resets_text(entry) -> str:
    """When this entry's soonest live window clears.

    Only a window with something in it has a reset, and the provider's own
    Retry-After outranks the window model when we were given one.
    """
    if entry.blocked_for is not None:
        return f"blocked {duration(entry.blocked_for)}"
    live = [gauge.resets_in for gauge in entry.gauges
            if gauge.used and gauge.resets_in is not None]
    return duration(min(live)) if live else "-"


_WINDOW_LABEL = {"rpm": "RPM 60s", "tpm": "TPM 60s", "rpd": "RPD 24h", "tpd": "TPD 24h"}


def _table_for(entries, label, now: float) -> str:
    """One table over anything carrying gauges: model summaries or pool rows.

    A quantity nobody here declares a ceiling for gets no column -- Gemini
    publishes no tokens-per-day, so that column was a heading over ten rows of
    "no cap".
    """
    names = declared_limits(entries)
    return _table([
        ["  MODEL", "CTX", *(_WINDOW_LABEL[name] for name in names),
         "REFUSED", "ERR", "RESETS", "LAST"],
        *[[
            f"  {label(entry)}",
            compact(entry.max_input_tokens) if entry.max_input_tokens else "-",
            *(_gauge_text(entry, name) for name in names),
            str(entry.day.rate_limited),
            str(entry.day.errors),
            _resets_text(entry),
            "never" if entry.last_call is None else ago(entry.last_call, now),
        ] for entry in entries],
    ])


def _table(rows: List[List[str]]) -> str:
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    lines = []
    for row in rows:
        # First column left-aligned (names), the rest right-aligned (numbers).
        cells = [cell.ljust(widths[0]) if column == 0 else cell.rjust(widths[column])
                 for column, cell in enumerate(row)]
        lines.append("  ".join(cells).rstrip())
    return "\n".join(lines)


def _header(report: Report) -> None:
    header = ("no calls recorded yet" if report.since is None
              else f"{report.calls} calls recorded since {stamp(report.since)}")
    print(f"Pool quota @ {stamp(report.generated)} -- {header}")
    print("Counted from this router's ledger. A window opens with its first "
          "attempt and RESETS one length later (60s / 24h).")
    print()
    for note in report.notes:
        print(f"! {note}")
    if report.notes:
        print()


def print_models(report: Report) -> None:
    """The default view: each model over every account that serves it."""
    _header(report)
    for platform in report.platforms:
        models = [model for model in report.models if model.platform == platform.platform]
        keys = ", ".join(platform.accounts) or "no accounts"
        print(f"{platform.platform} -- {platform.models} model(s) over {keys}; "
              f"last 24h: {platform.day.requests} req, "
              f"{compact(platform.day.tokens)} tok, "
              f"{platform.day.rate_limited} refused, {platform.day.errors} errored")

        def label(model):
            spread = "" if len(model.accounts) < 2 else f" ({len(model.accounts)} accounts)"
            return f"{model.model}{spread}"

        print(_table_for(models, label, report.generated))
        print()


def print_account(report: Report) -> None:
    """One account, member by member: what the filter narrows down to."""
    _header(report)
    for account in report.accounts:
        rows = [row for row in report.rows
                if row.account == account.account and row.platform == account.platform]
        print(f"{account.account} ({account.platform}) -- last 24h: "
              f"{account.day.requests} req, {compact(account.day.tokens)} tok, "
              f"{account.day.rate_limited} refused, {account.day.errors} errored")
        print(_table_for(rows, lambda row: row.model + ("" if row.configured
                                                        else " (retired)"),
                         report.generated))
        print()


def _only(report: Report, account: str) -> Report:
    """The report narrowed to one account.

    Narrowing rather than filtering at render time keeps `--json` and the panel
    honest: what a reader sees and what the data says are the same thing.
    """
    known = {summary.account for summary in report.accounts}
    if account not in known:
        raise SystemExit(f"No account {account!r} in the pool. Known: "
                         f"{', '.join(sorted(known)) or 'none'}")
    report.rows = [row for row in report.rows if row.account == account]
    report.accounts = [summary for summary in report.accounts
                       if summary.account == account]
    # The folds are rebuilt from what survived, so a narrowed report never
    # carries a model total that includes an account it no longer shows.
    report.models = _fold_models(report.rows)
    report.platforms = _fold_platforms(report.models, report.accounts)
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m llm_router.quota",
        description="What the pool has spent, and how close each account is to its wall.")
    parser.add_argument("command", nargs="?", default="status", choices=("status", "panel"),
                        help="status: the table (add --json for data). "
                             "panel: write the HTML snapshot and print its path.")
    parser.add_argument("--json", action="store_true",
                        help="status only: print the report as JSON")
    parser.add_argument("--account", help="report only this account, member by "
                                          "member, instead of every model summed "
                                          "over the accounts serving it")
    parser.add_argument("--dir", help="read from this usage directory "
                                      "(default: llm_router/.usage/)")
    parser.add_argument("--out", help="panel only: where to write the HTML")
    args = parser.parse_args(argv)

    directory = usage_dir(args.dir)
    report = build_report(read_ledger(directory), read_pool(directory))
    if args.account:
        report = _only(report, args.account)

    if args.command == "status":
        if args.json:
            print(json.dumps(asdict(report), indent=2, default=str))
        elif args.account:
            print_account(report)
        else:
            print_models(report)
        return 0

    out = Path(args.out) if args.out else directory / "panel.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_panel(report), encoding="utf-8")
    print(out.resolve())
    return 0
