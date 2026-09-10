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

from llm_router.base_provider import looks_decommissioned

# The RouterChatModel wrapper reports itself as a model too. Its calls are
# agent *steps*; the child calls underneath it are the real provider calls.
ROUTER_MODELS = {"router", "RouterChatModel"}

EDIT_TOOLS = {"edit_file", "write_file", "str_replace", "apply_patch"}

# The project's feedback file. "Notes in, notes out" -- the session reads it and
# appends its account to it -- is R7 in design/long-run-harness.md, and that note
# calls it "the whole human interface". Phase 2's north star is an agent that
# writes an account a human reviews instead of the code, so whether the account
# exists is the one thing about it that can be checked without reading it.
ACCOUNT_FILE = "NOTES.md"
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


def added_by_file(patch_text: str) -> dict:
    """Lines added per path, read off the diff.

    `diff_summary` counts additions across the whole patch, which cannot answer
    "did it write *there*". Keyed on the `+++ b/` header, so a file the agent
    only deleted from contributes nothing -- which is correct here: deleting the
    notes is not writing an account.
    """
    counts, current = {}, None
    for line in patch_text.splitlines():
        if line.startswith("+++ b/"):
            current = line[len("+++ b/"):].strip()
            counts.setdefault(current, 0)
        elif line.startswith(("+++", "---", "diff --git", "index ", "@@")):
            continue
        elif current and line.startswith("+"):
            counts[current] += 1
    return counts


def added_tests(patch_text: str) -> int:
    """Test functions the run added that nobody asked for.

    Counted off the diff, so it costs nothing and needs no scenario change. Four
    of nine runs in the first full-set batch did this, and in `stale-categories`
    and `bots-to-base-class` the added tests pinned exactly the bug and the
    invariant under test -- the behaviour a standing maintainer most needs, and
    the only thing the harness did with it was score it as tampering
    (docs/08-evaluation-method.md#85).

    Added lines only, and only inside a file pytest would collect: a `def
    test_...` moved between files is not a new test, and one written into a
    module that never runs is not a test at all.
    """
    total, current = 0, None
    for line in patch_text.splitlines():
        if line.startswith("+++ b/"):
            current = line[len("+++ b/"):].strip()
        elif line.startswith(("+++", "---", "diff --git", "index ", "@@")):
            continue
        elif (current and _collectible(current) and line.startswith("+")
              and line[1:].lstrip().startswith(("def test_", "async def test_"))):
            total += 1
    return total


def _collectible(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return name.endswith(".py") and (name.startswith("test_")
                                     or name.endswith("_test.py"))


def account_written(patch_text: str) -> bool:
    """Did the run add anything to the project's feedback file?

    Deliberately shallow: it asks whether the account exists, not whether it is
    any good. Three of nine runs in the first full-set batch added nothing to
    `NOTES.md` -- one of them on a task literally called `session-from-notes` --
    and a run that implemented an ambiguous reading consistently and never wrote
    the decision down scored p2p 6/6, which is silent competence and reads as
    success everywhere else in the record.
    """
    return added_by_file(patch_text).get(ACCOUNT_FILE, 0) > 0


def bounce_breakdown(events: list) -> tuple:
    """(bounces per model, models that went away) -- who is losing the pool time.

    `failover_bounces` is one number and says only that the run was long. What
    it never said is *which member*, and that turned out to matter: 135 of 247
    bounces in a 40-run batch were a single model answering 404, five times a
    run, in every run. The router handled it correctly and logged an ERROR
    naming the model to delete -- into a run nobody greps. It costs no tokens
    and fails no run, so this column is the only place it can surface.

    The model comes from the `llm_start` that shares the failed attempt's
    `run_id`; the verdict comes from the same matcher the router routes on, so
    the two cannot drift.
    """
    model_of = {e.get("run_id"): e.get("model") for e in events
                if e.get("event") == "llm_start"}
    per_model, retired = {}, set()
    for event in events:
        if event.get("event") != "llm_error":
            continue
        model = model_of.get(event.get("run_id")) or "unknown"
        per_model[model] = per_model.get(model, 0) + 1
        if looks_decommissioned(str(event.get("detail", ""))):
            retired.add(model)
    return per_model, sorted(retired)


def from_trace(events: list) -> dict:
    """Counts over the event stream."""
    llm_starts = [e for e in events if e.get("event") == "llm_start"]
    provider_starts = [e for e in llm_starts if e.get("model") not in ROUTER_MODELS]
    # The router returns the provider's message, including usage metadata.
    # Its llm_end repeats those tokens; counting both doubles research cost.
    router_ids = {e["run_id"] for e in llm_starts
                  if e.get("model") in ROUTER_MODELS and e.get("run_id")}
    usage_events = [e for e in events if e.get("run_id") not in router_ids]
    tokens_in = sum(e.get("tokens_in") or 0 for e in usage_events)
    tokens_out = sum(e.get("tokens_out") or 0 for e in usage_events)
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
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        # The cost driver, per call rather than per run. `tokens_in` alone
        # cannot tell a long run from an expensive one, and the audit that
        # produced this needed exactly that split: over 40 runs `tokens_in`
        # correlates +0.95 with `steps` and only +0.24 with `failover_bounces`,
        # so what a run spends is the conversation being re-sent every step,
        # not failover replaying it (docs/06-agent.md#611).
        "tokens_per_call": round(
            tokens_in / len(provider_starts))
        if provider_starts else 0,
        # Per call, because per run it only says the run was long. A bounce
        # spends a request against a daily budget and no input tokens: the
        # provider refuses a 429 or a 404 at the gate, so `llm_error` carries
        # no token count and there is none to carry.
        "bounces_per_call": round(
            sum(1 for e in events if e.get("event") == "llm_error")
            / len(provider_starts), 2) if provider_starts else 0.0,
        # No edit tool was ever called. The mechanical half of a failure the
        # harness cannot fully see: three recorded runs finished with an empty
        # tree and a closing message reporting the work as done -- "were
        # implemented", "has been fully implemented and tested", a heading
        # reading *Implemented Requirements* over three fields that do not
        # exist. Whether the prose claims completion needs a reader; whether
        # anything was edited does not, and it is the half that can be trusted.
        "edited_nothing": not any(
            e.get("event") == "tool_start" and e.get("tool") in EDIT_TOOLS
            for e in events),
        "bounce_models": bounce_breakdown(events)[0],
        # Members the pool dropped mid-run because they are gone upstream. A
        # retirement is correct behaviour and costs nothing measurable, which is
        # exactly why it went unnoticed for a month.
        "retired_models": bounce_breakdown(events)[1],
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
                     stderr: str = "", broken_files: list = ()) -> str:
    """Why a failed run failed. Empty string for a run that passed.

    Ordered most-external-cause first: a run killed by the step budget never
    got the chance to demonstrate a retrieval or reasoning failure.
    """
    # Tampering is an integrity verdict, not a capability one. Giving it a
    # failure class too would average it into the taxonomy counts, which is
    # exactly what tracking it separately is meant to prevent.
    # A protected file the agent left unrunnable is a tooling failure, and
    # checked before the integrity short-circuit below so it is not swallowed
    # by it. One recorded run wrote diff markers into a test body: the suite
    # stopped collecting, the run scored `tampered`, and the one run in the
    # batch with a textbook tooling failure recorded no failure class at all.
    if broken_files:
        return "tooling"
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
            outcome: str, stderr: str = "", has_account: bool = False,
            broken_files: list = ()) -> dict:
    events = load_trace(trace_path)
    gold = gold_files(reference_patch)
    summary = diff_summary(patch_text)
    touched = set(summary["diff_files"])

    metrics = {**from_trace(events), **summary}
    metrics["gold_files"] = sorted(gold)
    metrics["found_gold_file"] = _read_gold(events, gold)
    # None, not False, when the seed has no feedback file to append to: a
    # scenario that never offered one cannot be said to have skipped it, and
    # averaging those in would make the number look better the more scenarios
    # are added that do not test this at all.
    metrics["wrote_account"] = account_written(patch_text) if has_account else None
    metrics["added_tests"] = added_tests(patch_text)
    metrics["failure_class"] = classify_failure(outcome, events, gold, touched,
                                               stderr, broken_files)
    return metrics
