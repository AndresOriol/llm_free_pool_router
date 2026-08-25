"""The command line over the ledger.

Two readers, one report. `status` prints a table for a human glancing at a
terminal and, with `--json`, the same report as data -- which is how a coding
agent asks how much of the free tier is left before deciding to start something
long. `panel` writes the HTML snapshot.

Nothing here touches the network or spends a request: every figure comes off
`ledger.jsonl`, so an agent may ask as often as it likes.
"""

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional

from .format import ago, compact, duration, iso, percent, scaled
from .html import render_panel
from .ledger import read_ledger, read_pool, usage_dir
from .report import Report, Row, build_report


def _gauge_text(row: Row, name: str) -> str:
    gauge = next((candidate for candidate in row.gauges if candidate.name == name), None)
    if gauge is None:
        return "-"
    if gauge.limit is None or gauge.ratio is None:
        return f"{scaled(name, gauge.used)} (no cap)"
    return f"{scaled(name, gauge.used)}/{scaled(name, gauge.limit)} {percent(gauge.ratio)}"


def _resets_text(row: Row) -> str:
    """When this member's soonest live window clears.

    Only windows with something in them have a reset, and the provider's own
    Retry-After outranks the window model when we were given one.
    """
    if row.blocked_for is not None:
        return f"blocked {duration(row.blocked_for)}"
    live = [gauge.resets_in for gauge in row.gauges
            if gauge.used and gauge.resets_in is not None]
    return duration(min(live)) if live else "-"


def _table(rows: List[List[str]]) -> str:
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    lines = []
    for row in rows:
        # First column left-aligned (names), the rest right-aligned (numbers).
        cells = [cell.ljust(widths[0]) if column == 0 else cell.rjust(widths[column])
                 for column, cell in enumerate(row)]
        lines.append("  ".join(cells).rstrip())
    return "\n".join(lines)


def print_status(report: Report) -> None:
    header = ("no calls recorded yet" if report.since is None
              else f"{report.calls} calls recorded since {iso(report.since)}")
    print(f"Pool quota @ {iso(report.generated)} -- {header}")
    print("Counted from this router's ledger. A window opens with its first "
          "attempt and RESETS one length later (60s / 24h).")
    print()

    for note in report.notes:
        print(f"! {note}")
    if report.notes:
        print()

    for account in report.accounts:
        rows = [row for row in report.rows
                if row.account == account.account and row.platform == account.platform]
        print(f"{account.account} ({account.platform}) -- seen here in the last 24h: "
              f"{account.day.requests} req, {compact(account.day.tokens)} tok, "
              f"{account.day.rate_limited} rate-limited, {account.day.errors} errored")
        print(_table([
            ["  MODEL", "RPM 60s", "TPM 60s", "RPD 24h", "TPD 24h",
             "429", "ERR", "RESETS", "LAST"],
            *[[
                f"  {row.model}" + ("" if row.configured else " (retired)"),
                _gauge_text(row, "rpm"),
                _gauge_text(row, "tpm"),
                _gauge_text(row, "rpd"),
                _gauge_text(row, "tpd"),
                str(row.day.rate_limited),
                str(row.day.errors),
                _resets_text(row),
                "never" if row.last_call is None else ago(row.last_call, report.generated),
            ] for row in rows],
        ]))
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
    parser.add_argument("--account", help="report only this account")
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
        else:
            print_status(report)
        return 0

    out = Path(args.out) if args.out else directory / "panel.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_panel(report), encoding="utf-8")
    print(out.resolve())
    return 0
