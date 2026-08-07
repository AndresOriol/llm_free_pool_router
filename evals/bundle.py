"""Collecting a batch's evidence into one file, for the J2 analyst to read.

J2 does error analysis over a *batch*: it reads every failure and groups them
(docs/design/long-run-harness.md#62-two-judges-not-one). Doing that by globbing
seven files per run wastes most of a context window on directory listings, and
on a wide-context member that context is the scarce resource.

So the runner emits the bundle instead. It is ordered failures-first, because
that is the order the analysis works in, and every section is labelled with the
`run_id` it came from so a claim in the report can point at its evidence.
"""

import json
from pathlib import Path

# Per-run caps. The bundle exists to fit in one prompt; a single run's pytest
# output can otherwise crowd out ten other runs' diffs.
MAX_DIFF = 6_000
MAX_VERIFY = 3_000
MAX_STDOUT = 4_000
MAX_RATIONALE = 4_000

_HEADLINE = ("outcome", "failure_class", "f2p_passed", "f2p_total",
             "p2p_passed", "p2p_total", "provider_calls", "failover_bounces",
             "tokens_in", "wall_time_s", "models_used", "diff_files")


def _clip(text: str, limit: int, *, tail: bool = False) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return ("...(clipped)\n" + text[-limit:]) if tail else (text[:limit] + "\n...(clipped)")


def _read(path: Path, limit: int, *, tail: bool = False) -> str:
    if not path.is_file():
        return ""
    return _clip(path.read_text(encoding="utf-8", errors="replace"), limit, tail=tail)


def _run_section(record: dict, run_dir: Path) -> str:
    head = {key: record.get(key) for key in _HEADLINE}
    parts = [
        f"## {record['run_id']}",
        "",
        f"- scenario: `{record.get('scenario')}` / task `{record.get('task')}` "
        f"({record.get('difficulty')}, {record.get('category')})",
        f"- config: `{record.get('config')}` @ `{(record.get('config_sha') or '')[:8]}`"
        f"  rep {record.get('rep')}",
        f"- metrics: `{json.dumps(head)}`",
        f"- evidence directory: `{run_dir.as_posix()}`",
    ]
    if record.get("tampered_files"):
        parts.append(f"- **TAMPERED**: {record['tampered_files']}")

    for title, body, language in (
        ("diff", _read(run_dir / "diff.patch", MAX_DIFF), "diff"),
        # Tail-clipped: pytest puts the summary of what failed at the end.
        ("hidden-test output", _read(run_dir / "verify.txt", MAX_VERIFY, tail=True), ""),
        ("agent stdout (tail)", _read(run_dir / "stdout.log", MAX_STDOUT, tail=True), ""),
        ("session rationale", _read(run_dir / "rationale.md", MAX_RATIONALE), ""),
    ):
        if body:
            parts += ["", f"### {title}", "", f"```{language}", body, "```"]
    return "\n".join(parts)


def build(results_dir: Path, records: list) -> str:
    """One markdown document: the batch summary, then failures, then passes."""
    results_dir = Path(results_dir)
    failed = [r for r in records if r.get("outcome") != "pass"]
    passed = [r for r in records if r.get("outcome") == "pass"]

    lines = [
        "# Run bundle",
        "",
        f"{len(records)} run(s): {len(passed)} pass, {len(failed)} not.",
        "",
        "| run | config | outcome | class | f2p | p2p | calls | tokens_in |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: |",
    ]
    for record in records:
        lines.append(
            f"| `{record['run_id']}` | {record.get('config')} | "
            f"{record.get('outcome')} | {record.get('failure_class') or '-'} | "
            f"{record.get('f2p_passed')}/{record.get('f2p_total')} | "
            f"{record.get('p2p_passed')}/{record.get('p2p_total')} | "
            f"{record.get('provider_calls')} | {record.get('tokens_in')} |")

    # Failures first: they are what the analysis is for, and a truncated bundle
    # should lose passes rather than evidence.
    for group, title in ((failed, "Runs that did not pass"), (passed, "Runs that passed")):
        if not group:
            continue
        lines += ["", f"# {title}", ""]
        for record in group:
            lines += [_run_section(record, results_dir / "runs" / record["run_id"]), ""]
    return "\n".join(lines)
