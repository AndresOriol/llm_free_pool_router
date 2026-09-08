"""The evidence: every run this project has recorded, and how to read one.

A *record* is a directory a run left behind. There are two kinds and the
difference is whether anyone knows the right answer:

- **eval** -- written by [evals/run.py](../../evals/run.py) under
  `evals/results/runs/`. It has a `run.json`, so the verdict is ground truth:
  hidden tests decided it, not a model's opinion of itself.
- **live** -- written by an unattended or served run (`AGENT_TRACE_FILE`,
  `SERVE_RECORD_DIR`). No `run.json`, so no verdict. A finding here rests on
  reading the trajectory, which is weaker evidence and must be labelled as such.

Both kinds are searched, because the failure that matters most is the one that
only happens in the wild. What must never happen is quoting a live record as
though a test had passed on it, so `kind` travels with every row this module
returns.

**Nothing here parses console output.** Every field comes from `run.json`,
`trace.jsonl` (the flat event log) or `trace.json` (the condensed run tree) --
the same three files every automatic metric is summed over
([10. Metrics](../../docs/10-metrics.md)). `stderr.log` is searchable but is
never a source for a number.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

# Where eval runs land, relative to the project root. Not configurable: it is
# where `evals/run.py` writes, and a second name for it would be a second thing
# to keep true.
EVAL_RUNS = Path("evals") / "results" / "runs"

# Extra roots to scan for live records, comma-separated. Relative entries are
# resolved against the workdir, so a configuration can name `.agent_runs`
# without knowing where the project is mounted.
ROOTS_ENV = "IMPROVE_RECORDS"

# A directory is a record if it holds one of these.
_MARKERS = ("run.json", "trace.json", "trace.jsonl")

# How deep to look under a root. Eval runs sit one level down; the server writes
# `<record_dir>/<task_id>/trace.json`, also one level. Two is slack, not a
# guess -- and it stops a scan from walking a whole repository.
_MAX_DEPTH = 2

# Bytes of one file this module will hold in memory at once. Searching does not
# use it -- `_lines` streams -- so it applies only to the small metadata files.
#
# It used to apply to the traces too, at 4 MB, and that was a silent falsehood:
# a real 4.7 MB `trace.json` came back truncated, failed to parse, and was
# reported as "no trace.json in this record" -- the exact sentence that tells a
# reader the run was never traced. A cap that turns evidence into an absence is
# worse than no cap.
_MAX_READ = 1_000_000

# The searchable files of a record, in the order a reader would want them.
_SEARCHABLE = ("trace.jsonl", "trace.json", "stderr.log", "stdout.log")

_STAMP = re.compile(r"^(\d{8}T\d{6}Z)")


@dataclass
class Record:
    """One recorded run, and where its files are."""

    id: str
    path: Path
    kind: str                                      # "eval" | "live"
    verdict: dict = field(default_factory=dict)    # run.json, {} when live
    ts: str = ""                                   # ISO-8601 UTC, best available

    @property
    def has_events(self) -> bool:
        return (self.path / "trace.jsonl").is_file()

    @property
    def has_tree(self) -> bool:
        return (self.path / "trace.json").is_file()

    def file(self, name: str) -> Optional[Path]:
        found = self.path / name
        return found if found.is_file() else None

    def row(self) -> str:
        """One line, for a listing. Ordered so a scan down the column works."""
        v = self.verdict
        if self.kind == "eval":
            head = (f"{v.get('outcome', '?')}"
                    + (f"/{v['failure_class']}" if v.get("failure_class") else ""))
            detail = (f"cfg={v.get('config', '?')} "
                      f"task={v.get('scenario', '?')}/{v.get('task', '?')}")
        else:
            head = "live"
            detail = "no verdict"
        cost = (f"calls={v.get('provider_calls', '?')} "
                f"tok_in={v.get('tokens_in', '?')} "
                f"steps={v.get('steps', '?')}")
        return f"{self.id}  [{head}]  {detail}  {cost}  ({self.ts[:19]})"


def _read(path: Optional[Path], limit: int = _MAX_READ) -> str:
    if path is None or not path.is_file():
        return ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def _verdict(directory: Path) -> dict:
    raw = _read(directory / "run.json", 400_000)
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _stamp(directory: Path, verdict: dict) -> str:
    """When the run happened. The name first, because it is the only source
    that survives the directory being copied."""
    match = _STAMP.match(directory.name)
    if match:
        raw = match.group(1)
        return (f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}T"
                f"{raw[9:11]}:{raw[11:13]}:{raw[13:15]}+00:00")
    if verdict.get("ts"):
        return str(verdict["ts"])
    try:
        newest = max(p.stat().st_mtime for p in directory.iterdir() if p.is_file())
    except (OSError, ValueError):
        return ""
    return datetime.fromtimestamp(newest, timezone.utc).isoformat()


def default_roots(workdir: Path) -> list:
    """Where to look for records, eval runs first."""
    workdir = Path(workdir)
    roots = [workdir / EVAL_RUNS]
    for entry in (os.environ.get(ROOTS_ENV) or "").split(","):
        entry = entry.strip()
        if entry:
            path = Path(entry)
            roots.append(path if path.is_absolute() else workdir / path)
    return roots


def discover(roots: Iterable[Path]) -> list:
    """Every record under these roots, newest first.

    A root that does not exist is skipped in silence: `evals/results/runs/` is
    absent on a fresh clone and a live root is absent until something has run
    unattended, and neither is a fault to report at start-up.
    """
    found: dict = {}
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for directory in _walk(root, _MAX_DEPTH):
            if directory.name in found:
                continue
            verdict = _verdict(directory)
            found[directory.name] = Record(
                id=directory.name,
                path=directory,
                kind="eval" if verdict else "live",
                verdict=verdict,
                ts=_stamp(directory, verdict))
    return sorted(found.values(), key=lambda r: (r.ts, r.id), reverse=True)


def _walk(root: Path, depth: int) -> list:
    """Directories under `root` that look like a record, breadth-first."""
    out, frontier = [], [root]
    for _ in range(depth):
        following = []
        for parent in frontier:
            try:
                children = sorted(p for p in parent.iterdir() if p.is_dir())
            except OSError:
                continue
            for child in children:
                if any((child / marker).is_file() for marker in _MARKERS):
                    out.append(child)
                else:
                    following.append(child)
        frontier = following
    return out


def load(workdir: Path, run_id: str) -> Optional[Record]:
    """One record by id, or None."""
    for record in discover(default_roots(workdir)):
        if record.id == run_id:
            return record
    return None


# -- reading one record ----------------------------------------------------

def _lines(path: Optional[Path]):
    """Stream one file's lines, or nothing. Never loads the whole thing.

    A `trace.jsonl` runs to megabytes and a `trace.json` to tens of them, and
    every search here is a single pass, so streaming costs nothing and removes
    the only reason there would be a cap to get wrong.
    """
    if path is None or not path.is_file():
        return
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                yield line.rstrip("\n")
    except OSError:
        return


def events(record: Record, limit: int = 0) -> list:
    """`trace.jsonl` as objects, tolerating a truncated last line.

    Truncation is the normal case, not the exceptional one: the eval runner
    kills a run that overruns its timeout, and those are the runs worth reading.
    """
    out = []
    for line in _lines(record.file("trace.jsonl")):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out[-limit:] if limit else out


def tree(record: Record) -> dict:
    """`trace.json` as an object, or `{}` when it is absent or unparseable.

    `has_tree` is what distinguishes the two, and callers must use it: "there is
    no run tree" and "the run tree would not parse" send a reader to different
    places, and only one of them is about the run.
    """
    path = record.file("trace.json")
    if path is None:
        return {}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            loaded = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        logging.getLogger("harness.improve").warning(
            f"{path} would not parse: {exc!r}")
        return {}
    return loaded if isinstance(loaded, dict) else {}


def routing_gaps(record: Record, top: int = 3) -> list:
    """The longest silences between provider calls, in seconds.

    The sharpest instrument in the `trace-reviewer` method, and the reason it is
    computed here rather than left to be eyeballed: `stopping` is one failure
    class covering a loop that never settled, a run that ran out of clock, a
    single provider call that hung, and a starved pool. One recorded run made 2
    calls in 2,722 seconds. A long final gap says the agent was not the problem,
    and nothing else in the record distinguishes that from bad judgement.
    """
    stamps = [e["ts"] for e in events(record)
              if e.get("event") == "llm_start"
              and isinstance(e.get("ts"), (int, float))]
    if len(stamps) < 2:
        return []
    gaps = sorted(((round(b - a, 1), i) for i, (a, b) in
                   enumerate(zip(stamps, stamps[1:]), start=1)), reverse=True)
    return [f"{seconds}s before call {index + 1}" for seconds, index in gaps[:top]]


def grep(record: Record, pattern: str, max_hits: int = 8) -> list:
    """Matching lines across this record's trace, clipped.

    Searches the flat event log, the run tree, the router's narration and the
    agent's own account. Raises `re.error` on a bad pattern, so a mistyped
    regex is reported to whoever wrote it rather than silently matching nothing.
    """
    regex = re.compile(pattern)
    hits = []
    for name in _SEARCHABLE:
        for line in _lines(record.file(name)):
            if regex.search(line):
                hits.append(f"{name}: {line.strip()[:400]}")
                if len(hits) >= max_hits:
                    return hits
    return hits


def lever_changed_at(workdir: Path, lever: str) -> Optional[str]:
    """When the file an issue names as its lever last changed, ISO-8601 UTC.

    The one fact that separates "this is happening" from "this already got
    fixed and the traces are older than the fix", and it is not in any trace.

    The first live pass got that wrong in the most convincing way available: it
    found two runs crashing with `GraphRecursionError`, diagnosed a 120-step
    limit in `agent/code/session.py`, and delegated a fix -- for a change that
    had shipped the day before the runs it was reading were even superseded.
    The evidence was real, the citation was correct, the signature was
    well-formed, and the whole issue was stale. A delegation was spent, and the
    coding agent then reported having made changes the diff does not contain.

    None of that is visible from the runs. It is visible in one `git log`.
    """
    lever = (lever or "").strip().lstrip("/")
    if not lever:
        return None
    import subprocess
    try:
        done = subprocess.run(
            ("git", "log", "-1", "--format=%cI", "--", lever),
            cwd=str(workdir), capture_output=True, encoding="utf-8",
            errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return (done.stdout or "").strip() or None


def contains(record: Record, pattern: str) -> bool:
    """Does anything in this record's trace match? One streaming pass."""
    regex = re.compile(pattern)
    return any(regex.search(line)
               for name in _SEARCHABLE
               for line in _lines(record.file(name)))


# -- matching a signature --------------------------------------------------

_OPS = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
        "!=": lambda a, b: a != b, ">": lambda a, b: a > b,
        "<": lambda a, b: a < b, "=": lambda a, b: a == b}


def _clause(actual, expected) -> bool:
    """One `where` clause. `expected` may be `"> 200000"` or a literal."""
    if isinstance(expected, str):
        for op, test in _OPS.items():
            if expected.startswith(op):
                operand = expected[len(op):].strip()
                try:
                    return test(float(actual), float(operand))
                except (TypeError, ValueError):
                    return test(str(actual), operand)
    if isinstance(actual, list):
        return expected in actual
    if isinstance(actual, bool) or isinstance(expected, bool):
        return bool(actual) == bool(expected)
    try:
        return float(actual) == float(expected)
    except (TypeError, ValueError):
        return str(actual) == str(expected)


def matches(record: Record, signature: dict) -> bool:
    """Does this record show the failure the signature names?

    A signature is deliberately small: `kind`, `where` clauses over `run.json`
    fields, and one `grep` regex over the trace. It is declarative so that a
    *stored* issue can be re-checked months later by a session that never read
    the trace that produced it -- which is the whole point of the ledger. Code
    would be more expressive and could not be replayed safely.

    A `where` clause naming a field the record does not have never matches. That
    is what keeps a live record, which has no `run.json` at all, from silently
    satisfying a signature written about eval verdicts.
    """
    signature = signature or {}
    wanted_kind = signature.get("kind")
    if wanted_kind and wanted_kind != record.kind:
        return False

    for key, expected in (signature.get("where") or {}).items():
        if key not in record.verdict:
            return False
        if not _clause(record.verdict[key], expected):
            return False

    pattern = signature.get("grep")
    if pattern and not contains(record, pattern):
        return False

    # An empty signature would match every run ever recorded, which reads as
    # "this failure is everywhere" rather than as the empty question it is.
    return bool(wanted_kind or signature.get("where") or pattern)
