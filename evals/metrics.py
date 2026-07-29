"""Deriving metrics from a run's JSONL trace, diff, and verification result.

Everything here is a count or a sum over trace.jsonl (written by the agent's
EVAL_TRACE_FILE handler). No log scraping, so a change to the agent's console
output can never silently break a metric.

The failure taxonomy is the point of this module. A pass rate says a config is
worse; the taxonomy says what to fix, and the fixes are unrelated to each
other -- better navigation tools, a different edit format, a stronger model
tier, or a loop change.
"""

import json
import re
from pathlib import Path

# The RouterChatModel wrapper reports itself as a model too. Its calls are
# agent *steps*; the child calls underneath it are the real provider calls.
ROUTER_MODELS = {"router", "RouterChatModel"}

EDIT_TOOLS = {"edit_file", "write_file", "str_replace", "apply_patch"}
READ_TOOLS = {"read_file", "ls", "glob", "grep", "search"}


def load_trace(path: Path) -> list:
    """Parse trace.jsonl, tolerating a truncated final line from a killed run."""
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def gold_files(patch_text: str) -> set:
    """Paths the reference solution touches -- the ground truth for retrieval."""
    found = set()
    for match in re.finditer(r"^\+\+\+ b/(.+)$", patch_text, re.MULTILINE):
        name = match.group(1).strip()
        if name != "/dev/null":
            found.add(name)
    return found


def diff_summary(patch_text: str) -> dict:
    added = len(re.findall(r"^\+(?!\+\+)", patch_text, re.MULTILINE))
    removed = len(re.findall(r"^-(?!--)", patch_text, re.MULTILINE))
    files = gold_files(patch_text)
    return {"files_touched": len(files), "diff_lines": added + removed,
            "diff_added": added, "diff_removed": removed,
            "diff_files": sorted(files)}


def from_trace(events: list) -> dict:
    """Counts over the event stream."""
    llm_starts = [e for e in events if e.get("event") == "llm_start"]
    provider_starts = [e for e in llm_starts if e.get("model") not in ROUTER_MODELS]
    tool_starts = [e for e in events if e.get("event") == "tool_start"]
    tool_errors = [e for e in events if e.get("event") == "tool_error"]

    # A tool that returned an error string rather than raising still failed.
    soft_errors = [e for e in events if e.get("event") == "tool_end"
                   and str(e.get("output", "")).lstrip().startswith("Error")]

    test_runs = [e for e in tool_starts
                 if e.get("tool") == "execute" and "pytest" in str(e.get("args", ""))]

    return {
        "steps": len(llm_starts) - len(provider_starts),
        "provider_calls": len(provider_starts),
        "failover_bounces": sum(1 for e in events if e.get("event") == "llm_error"),
        "tokens_in": sum(e.get("tokens_in") or 0 for e in events),
        "tokens_out": sum(e.get("tokens_out") or 0 for e in events),
        "tool_calls": len(tool_starts),
        "bad_tool_calls": len(tool_errors) + len(soft_errors),
        "models_used": sorted({e.get("model") for e in provider_starts if e.get("model")}),
        "ran_own_tests": bool(test_runs),
        "self_corrected": _self_corrected(events),
    }


def _self_corrected(events: list) -> bool:
    """Did a failing test run get followed by another edit?

    The difference between an agent and a code generator: noticing its own
    output was wrong and acting on it.
    """
    seen_failure = False
    for event in events:
        if event.get("event") == "tool_end" and event.get("tool") == "execute":
            output = str(event.get("output", ""))
            if "fail" in output.lower() or "error" in output.lower():
                seen_failure = True
        elif (seen_failure and event.get("event") == "tool_start"
              and event.get("tool") in EDIT_TOOLS):
            return True
    return False


def _read_gold(events: list, gold: set) -> bool:
    """Did the agent ever look at a file the reference solution touches?"""
    if not gold:
        return True  # nothing to find; don't claim a retrieval failure
    for event in events:
        if event.get("event") != "tool_start":
            continue
        args = str(event.get("args", ""))
        if any(path in args or Path(path).name in args for path in gold):
            return True
    return False


def classify_failure(outcome: str, events: list, gold: set, touched: set,
                     stderr: str = "") -> str:
    """Why a failed run failed. Empty string for a run that passed.

    Ordered most-external-cause first: a run killed by the step budget never
    got the chance to demonstrate a retrieval or reasoning failure.
    """
    # Tampering is an integrity verdict, not a capability one. Giving it a
    # failure class too would average it into the taxonomy counts, which is
    # exactly what tracking it separately is meant to prevent.
    if outcome in {"pass", "tampered"}:
        return ""
    if outcome in {"timeout", "crash"} or "GraphRecursionError" in stderr:
        return "stopping"
    if not _read_gold(events, gold):
        return "retrieval"

    edit_failures = sum(
        1 for e in events
        if e.get("event") in {"tool_error", "tool_end"}
        and e.get("tool") in EDIT_TOOLS
        and (e.get("ok") is False
             or str(e.get("output", "")).lstrip().startswith("Error")))

    # Found the code but landed no edit on it: the model knew where to go and
    # couldn't express the change.
    if edit_failures and not (gold & touched):
        return "tooling"
    if not touched:
        return "stopping" if not edit_failures else "tooling"
    return "reasoning"


def collect(trace_path: Path, patch_text: str, reference_patch: str,
            outcome: str, stderr: str = "") -> dict:
    events = load_trace(trace_path)
    gold = gold_files(reference_patch)
    summary = diff_summary(patch_text)
    touched = set(summary["diff_files"])

    metrics = {**from_trace(events), **summary}
    metrics["gold_files"] = sorted(gold)
    metrics["found_gold_file"] = _read_gold(events, gold)
    metrics["failure_class"] = classify_failure(outcome, events, gold, touched, stderr)
    return metrics
