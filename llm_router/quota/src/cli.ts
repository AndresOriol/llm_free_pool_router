/**
 * The command line over the ledger.
 *
 * Two readers, one report. `status` prints a table for a human glancing at a
 * terminal and, with `--json`, the same report as data -- which is how a coding
 * agent asks how much of the free tier is left before deciding to start
 * something long. `panel` writes the HTML snapshot.
 *
 * Runs straight from source: Node strips the types, so there is no build step
 * to remember and no compiled copy to go stale against the .ts files.
 */

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

import { readLedger, readPool, usageDir } from "./ledger.ts";
import { buildReport, tokens } from "./report.ts";
import type { Report, Row } from "./report.ts";
import { renderPanel } from "./html.ts";
import { ago, compact, iso, percent } from "./format.ts";

const USAGE = `Usage: node llm_router/quota/src/cli.ts <command> [options]

Commands:
  status              What each account and model has consumed, against its limits
  panel               Write the HTML snapshot and print its path

Options:
  --json              status only: print the report as JSON
  --dir <path>        Read the ledger from here (default: llm_router/.usage/)
  --out <path>        panel only: where to write (default: <dir>/panel.html)
  -h, --help          This text
`;

interface Options {
  command: string;
  json: boolean;
  dir?: string;
  out?: string;
}

function parseArgs(argv: string[]): Options {
  const options: Options = { command: argv[0] ?? "status", json: false };
  for (let i = 1; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--json") options.json = true;
    else if (arg === "--dir") options.dir = argv[++i];
    else if (arg === "--out") options.out = argv[++i];
    else throw new Error(`Unknown option: ${arg}`);
  }
  return options;
}

function gaugeText(row: Row, name: "rpm" | "tpm" | "rpd" | "tpd"): string {
  const gauge = row.gauges.find((candidate) => candidate.name === name);
  if (!gauge) return "-";
  const scale = name.startsWith("t") ? compact : (value: number) => String(value);
  if (gauge.limit === null || gauge.ratio === null) return `${scale(gauge.used)} (no cap)`;
  return `${scale(gauge.used)}/${scale(gauge.limit)} ${percent(gauge.ratio)}`;
}

function table(rows: string[][]): string {
  const widths = rows[0].map((_, column) =>
    Math.max(...rows.map((row) => row[column].length)));
  return rows
    .map((row) => row
      // First column left-aligned (names), the rest right-aligned (numbers).
      .map((cell, column) => column === 0
        ? cell.padEnd(widths[column])
        : cell.padStart(widths[column]))
      .join("  ")
      .trimEnd())
    .join("\n");
}

function printStatus(report: Report): void {
  const now = report.generated;
  const header = report.since === null
    ? "no calls recorded yet"
    : `${report.calls} calls recorded since ${iso(report.since)}`;
  console.log(`Pool quota @ ${iso(now)} -- ${header}`);
  console.log("RPM/TPM = rolling 60s, RPD/TPD = rolling 24h, counted from this machine's ledger.\n");

  for (const note of report.notes) console.log(`! ${note}`);
  if (report.notes.length > 0) console.log("");

  for (const account of report.accounts) {
    const rows = report.rows.filter((row) =>
      row.account === account.account && row.platform === account.platform);
    console.log(`${account.account} (${account.platform}) -- last 24h: `
      + `${account.day.requests} req, ${compact(tokens(account.day))} tok, `
      + `${account.day.rateLimited} rate-limited, ${account.day.errors} errored`);
    console.log(table([
      ["  MODEL", "RPM 60s", "TPM 60s", "RPD 24h", "TPD 24h", "429", "ERR", "LAST"],
      ...rows.map((row) => [
        `  ${row.model}${row.configured ? "" : " (retired)"}`,
        gaugeText(row, "rpm"),
        gaugeText(row, "tpm"),
        gaugeText(row, "rpd"),
        gaugeText(row, "tpd"),
        String(row.day.rateLimited),
        String(row.day.errors),
        row.lastCall === null ? "never" : ago(row.lastCall, now),
      ]),
    ]));
    console.log("");
  }
}

function main(argv: string[]): number {
  if (argv.includes("-h") || argv.includes("--help") || argv.length === 0) {
    console.log(USAGE);
    return 0;
  }

  let options: Options;
  try {
    options = parseArgs(argv);
  } catch (error) {
    console.error(`${(error as Error).message}\n\n${USAGE}`);
    return 2;
  }

  const dir = usageDir(options.dir);
  const report = buildReport(readLedger(dir), readPool(dir));

  if (options.command === "status") {
    if (options.json) console.log(JSON.stringify(report, null, 2));
    else printStatus(report);
    return 0;
  }

  if (options.command === "panel") {
    const out = resolve(options.out ?? join(dir, "panel.html"));
    mkdirSync(dirname(out), { recursive: true });
    writeFileSync(out, renderPanel(report), "utf8");
    console.log(out);
    return 0;
  }

  console.error(`Unknown command: ${options.command}\n\n${USAGE}`);
  return 2;
}

process.exitCode = main(process.argv.slice(2));
