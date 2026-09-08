"""The six tools the improvement loop needs, and nothing else.

Tool schemas are 91% of what a step of this loop spends
([6.4](../../docs/06-agent.md#64-why-it-is-shaped-this-way)), so the count here
is a budget rather than a preference. Each one is a stage of the loop:

| Tool | Stage |
| --- | --- |
| `find_runs` | detect — which runs exist, and which show a pattern |
| `read_run` | diagnose — one run, one section at a time |
| `write_issue` | name it, with a signature that can be re-checked |
| `delegate_fix` | hand the fix to `agent/code`, recorded against the issue |
| `run_evals` | make new evidence, on the branch the fix landed on |
| `check_issue` | replay the signature; close it, or reopen it |

Two shapes are deliberate and both are about the pool that serves this agent.

**Every parameter is a string or an int.** The members here are free-tier models
and a nested object schema is the thing they get wrong; a signature arrives as
JSON text and is parsed with an error the model can read and correct.

**Nothing returns a whole file.** `trace.json` is ~250 KB for a 17-turn run and
`read_file` on one would spend a session's context on a single run. Every
section below is bounded, and says what it clipped.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

from agent.improve import issues as issues_mod
from agent.improve import records as records_mod

logger = logging.getLogger("harness.improve")

MAX_OUTPUT = 8_000

# How long one `run_evals` call may take. A batch is the expensive thing this
# agent can do -- real free-tier quota, real wall time -- so it is bounded here
# as well as by the runner's own per-run timeout, and the ceiling is stated to
# the model in the tool description rather than discovered by hitting it.
TIMEOUT_ENV = "IMPROVE_EVAL_TIMEOUT"
DEFAULT_TIMEOUT = 3_600
# Runs per call. Verification wants a handful of runs on one scenario, not a
# batch: pass rates at these sample sizes are noise
# ([6.4.2](../../docs/06-agent.md#642-the-pass-column-is-noise)), so a bigger
# number here would buy confidence the numbers cannot carry.
MAX_REPS = 3


def _clip(text: str, limit: int = MAX_OUTPUT) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...(clipped, {len(text):,} chars total)"


def _tail(text: str, lines: int) -> str:
    return "\n".join((text or "").splitlines()[-lines:])


def make_tools(workdir: Path, transport=None) -> dict:
    """Build the tool set bound to one project. Returns name -> tool.

    `transport` is the peers transport carrying `code`. Without one there is no
    `delegate_fix`, and the agent can diagnose but not fix -- which is a usable
    read-only mode and not an error, so the tool is absent rather than present
    and certain to refuse ([agent/protocol/peers.py](../protocol/peers.py)).
    """
    from langchain_core.tools import StructuredTool

    workdir = Path(workdir)
    store = issues_mod.IssueStore(workdir)

    def _records() -> list:
        return records_mod.discover(records_mod.default_roots(workdir))

    # -- detect ------------------------------------------------------------

    def find_runs(pattern: str = "", kind: str = "", since: str = "",
                  config: str = "", outcome: str = "", limit: int = 20) -> str:
        """Find recorded runs, optionally the ones whose trace matches a regex.

        Filters: kind `eval` (has a verdict from hidden tests) or `live` (an
        unattended or served run, no verdict); `since` a UTC prefix like
        `2026-09-07`; `config` and `outcome` read from run.json. `pattern` is a
        Python regex searched across the run's trace, router narration and final
        message -- the way to ask "how many runs did X". Returns one line per
        run, newest first, with matching lines under each hit.
        """
        try:
            found = _records()
        except OSError as exc:
            return f"error: could not scan for runs: {exc}"

        if kind:
            found = [r for r in found if r.kind == kind]
        if since:
            found = [r for r in found if r.ts >= since]
        if config:
            found = [r for r in found if r.verdict.get("config") == config]
        if outcome:
            found = [r for r in found if r.verdict.get("outcome") == outcome]

        header = ""
        if pattern:
            try:
                hits = {r.id: records_mod.grep(r, pattern, 3) for r in found}
            except re.error as exc:
                return f"error: {pattern!r} is not a valid regex: {exc}"
            matched = [r for r in found if hits[r.id]]
            header = (f"{len(matched)} of {len(found)} run(s) match "
                      f"{pattern!r}.\n\n")
            found = matched
        else:
            header = f"{len(found)} run(s).\n\n"

        if not found:
            return header + "Nothing matched. Widen the filters, or say so."

        lines = []
        for record in found[:max(1, min(limit, 60))]:
            lines.append(record.row())
            if pattern:
                lines += [f"      {line}" for line in hits[record.id]]
        if len(found) > limit:
            lines.append(f"...({len(found) - limit} more; raise `limit` or "
                         f"narrow the filters)")
        return _clip(header + "\n".join(lines))

    # -- diagnose ----------------------------------------------------------

    def read_run(run_id: str, section: str = "summary") -> str:
        """Read one recorded run, one section at a time.

        Sections: `summary` (verdict, cost, tool mix, the longest silences
        between provider calls), `turns` (the spine of the run tree: one line
        per model call with its context size), `turn:<n>` (that call's context
        tail, what it answered and what its tools returned), `events` (the flat
        tool/model timeline), `diff`, `verify` (the hidden tests' output),
        `account` (what the agent itself claimed -- never evidence on its own).
        """
        record = records_mod.load(workdir, run_id)
        if record is None:
            return (f"error: no run called {run_id!r}. Use `find_runs` to list "
                    f"what exists.")
        try:
            return _clip(_section(record, section.strip() or "summary"))
        except OSError as exc:
            return f"error: could not read {run_id}: {exc}"

    # -- name it -----------------------------------------------------------

    def write_issue(title: str, id: str = "", status: str = "",
                    severity: str = "", summary: str = "", diagnosis: str = "",
                    fix: str = "", lever: str = "", signature: str = "",
                    evidence: str = "", note: str = "") -> str:
        """Create or amend one issue in the ledger. Returns the issue.

        An issue is a *pattern*, so `evidence` (space- or comma-separated run
        ids) should name at least two runs; one instance is an anecdote. `lever`
        names the file or knob a fix would land in — an issue whose fix is "be
        smarter" is not an issue. `severity` is low|medium|high.

        `signature` is JSON and is what later closes or reopens this, so write
        it to match the failure and nothing else. Keys: `kind` ("eval"/"live"),
        `where` (run.json fields, values either literal or like "> 200000"),
        `grep` (a regex over the trace). Example:
        {"where": {"failure_class": "stopping"}, "grep": "GraphRecursionError"}
        """
        fields = {"title": title, "note": note}
        for key, value in (("id", id), ("status", status),
                           ("severity", severity), ("summary", summary),
                           ("diagnosis", diagnosis), ("fix", fix),
                           ("lever", lever)):
            if value:
                fields[key] = value

        if signature:
            try:
                parsed = json.loads(signature)
            except json.JSONDecodeError as exc:
                return f"error: `signature` is not valid JSON ({exc}). Nothing written."
            if not isinstance(parsed, dict):
                return "error: `signature` must be a JSON object. Nothing written."
            fields["signature"] = parsed

        ids = [part for part in re.split(r"[,\s]+", evidence or "") if part]
        if ids:
            fields["evidence"] = ids

        try:
            issue = issues_mod.upsert(store, fields)
        except (ValueError, OSError) as exc:
            return f"error: {exc}"

        # Stamped from the runs that actually match, not from the ones the model
        # listed: an issue's date range is a claim about the signature.
        if issue.signature:
            issues_mod.stamp_evidence(
                issue, [r for r in _records()
                        if records_mod.matches(r, issue.signature)])
            store.save(issue)
        return f"Wrote `{store.path(issue.id)}`.\n\n{issue.render()}"

    # -- fix ---------------------------------------------------------------

    def delegate_fix(issue_id: str, brief: str) -> str:
        """Hand one issue's fix to the coding agent and record the delegation.

        This runs a whole coding session against this project, so it is slow and
        spends the shared free-tier budget — one well-specified brief beats
        three vague ones. Write it for someone who cannot see your conversation:
        the behaviour to change, the file or knob to change it in, what evidence
        says so, and what must keep working. It commits on its own branch; the
        report names the branch and the files that moved.

        You do not edit the harness yourself. The diff is reviewable against the
        diagnosis only if a different agent wrote it.
        """
        issue = store.get(issue_id)
        if issue is None:
            return (f"error: no issue called {issue_id!r}. Open one with "
                    f"`write_issue` first — a fix with no issue cannot be "
                    f"verified afterwards.")
        if not brief.strip():
            return "error: the brief is empty."

        from agent.protocol.local import render
        from agent.protocol.types import Message

        request = (f"{brief.strip()}\n\n"
                   f"---\nThis addresses issue `{issue.id}` — {issue.title}. "
                   f"The diagnosis behind it is in "
                   f"`{issues_mod.ISSUES_DIR.as_posix()}/{issue.id}.json`.")
        task = transport.message_send("code", Message.user(request))

        issue.tasks.append({"ts": issues_mod.now(), "task_id": task.id,
                            "agent": "code", "state": task.state,
                            "brief": brief.strip()[:1000]})
        if issue.status in (issues_mod.OPEN, issues_mod.REOPENED):
            issue.note("status", f"{issue.status} -> {issues_mod.FIXING}")
            issue.status = issues_mod.FIXING
        issue.note("delegated", f"task {task.id[:8]} to code")
        store.save(issue)

        return (f"{render(task, 'code')}\n\nRecorded against `{issue.id}`, now "
                f"**{issue.status}**. It is not fixed until runs recorded after "
                f"this stop matching its signature — `run_evals`, then "
                f"`check_issue`.")

    # -- make new evidence -------------------------------------------------

    def run_evals(config: str = "code", scenario: str = "", reps: int = 1,
                  ref: str = "", topic: str = "") -> str:
        """Record fresh runs, so a fix can be checked against evidence.

        Launches `python -m evals run`. This is the expensive call in the loop:
        it runs real agent sessions serially against the free-tier pool and can
        take an hour. Name a `scenario` (or a `topic`) — a whole-suite batch is
        a human's decision, not a tool call.

        `ref` is the branch or SHA to measure. A fix the coding agent just
        committed is on its branch, and a configuration pins `master`, so
        verifying a fix means naming that branch here. Returns the run ids
        recorded, which are what `check_issue` then reads.
        """
        if not scenario and not topic:
            return ("error: name a `scenario` or a `topic`. Running the whole "
                    "suite from a tool call would spend hours of quota on runs "
                    "nobody asked for.")
        reps = max(1, min(int(reps or 1), MAX_REPS))
        timeout = int(os.environ.get(TIMEOUT_ENV) or DEFAULT_TIMEOUT)

        before = {r.id for r in _records()}
        command = [sys.executable, "-m", "evals", "run", "--config", config,
                   "--reps", str(reps)]
        if scenario:
            command += ["--scenario", scenario]
        if topic:
            command += ["--topic", topic]
        if ref:
            command += ["--ref", ref]

        logger.info(f"Recording runs: {' '.join(command)}")
        try:
            done = subprocess.run(command, cwd=str(workdir), capture_output=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=timeout)
            output, code = (done.stdout or "") + (done.stderr or ""), done.returncode
        except subprocess.TimeoutExpired:
            output, code = "", None
        except OSError as exc:
            return f"error: could not launch the eval runner: {exc}"

        fresh = [r for r in _records() if r.id not in before]
        head = (f"The batch exceeded the {timeout}s ceiling and was stopped. "
                f"{len(fresh)} run(s) had been recorded by then."
                if code is None else
                f"`evals run` exited {code}. {len(fresh)} new run(s).")
        rows = "\n".join(r.row() for r in fresh) or "(nothing was recorded)"
        return _clip(f"{head}\n\n{rows}\n\n--- runner output (tail) ---\n"
                     + _tail(output, 40))

    # -- close it, or reopen it -------------------------------------------

    def check_issue(issue_id: str = "", since: str = "") -> str:
        """Replay an issue's signature over the runs and update its status.

        With no `issue_id`, lists the ledger instead. `since` is a UTC prefix
        bounding what counts as evidence; left empty it defaults to when the
        fix was delegated, which is the only boundary that answers "did it stop
        happening". An issue never closes because nobody looked: with no run
        recorded after that moment, the status is left alone and this says so.
        """
        if not issue_id:
            return store.summary()

        issue = store.get(issue_id)
        if issue is None:
            return f"error: no issue called {issue_id!r}.\n\n{store.summary()}"
        if not issue.signature:
            return (f"`{issue.id}` has no signature, so there is nothing to "
                    f"replay. Give it one with `write_issue` — an issue that "
                    f"cannot be re-checked cannot be closed.")

        boundary = since or (issue.tasks[-1]["ts"] if issue.tasks else "")
        was = issue.status
        report = issues_mod.check(issue, _records(), boundary)
        store.save(issue)

        if not report["considered"]:
            return (f"`{issue.id}` is still **{issue.status}**. No run has been "
                    f"recorded since {boundary or 'the beginning'}, so the fix "
                    f"is unverified — record some with `run_evals`.")
        matching = ", ".join(f"`{r}`" for r in report["matching_runs"]) or "none"
        return (f"`{issue.id}`: {report['matched']} of {report['considered']} "
                f"run(s) since {boundary or 'the beginning'} still match.\n"
                f"Status {was} -> **{issue.status}**.\nStill matching: {matching}")

    made = {
        "find_runs": StructuredTool.from_function(
            func=find_runs, name="find_runs", description=find_runs.__doc__),
        "read_run": StructuredTool.from_function(
            func=read_run, name="read_run", description=read_run.__doc__),
        "write_issue": StructuredTool.from_function(
            func=write_issue, name="write_issue", description=write_issue.__doc__),
        "run_evals": StructuredTool.from_function(
            func=run_evals, name="run_evals", description=run_evals.__doc__),
        "check_issue": StructuredTool.from_function(
            func=check_issue, name="check_issue", description=check_issue.__doc__),
    }
    if transport is not None and "code" in transport.registry.names:
        made["delegate_fix"] = StructuredTool.from_function(
            func=delegate_fix, name="delegate_fix",
            description=delegate_fix.__doc__)
    else:
        logger.warning("No `code` peer is reachable; this pass can diagnose "
                       "but not fix.")
    return made


# -- the sections of one run ----------------------------------------------

def _section(record, section: str) -> str:
    if section.startswith("turn:"):
        return _turn(record, section.split(":", 1)[1])
    renderers = {"summary": _summary, "turns": _turns, "events": _events,
                 "diff": lambda r: _file(r, "diff.patch"),
                 "verify": lambda r: _file(r, "verify.txt", tail=60),
                 "account": lambda r: _file(r, "stdout.log", tail=60)}
    render = renderers.get(section)
    if render is None:
        return (f"error: no section {section!r}. Try: "
                f"{', '.join(sorted(renderers))}, or turn:<n>.")
    return render(record)


def _file(record, name: str, tail: int = 0) -> str:
    path = record.file(name)
    if path is None:
        return f"`{name}` is not in this run's record."
    text = path.read_text(encoding="utf-8", errors="replace")
    return _tail(text, tail) if tail else text


def _summary(record) -> str:
    v = record.verdict
    lines = [f"# {record.id}", "",
             f"- **Kind:** {record.kind}"
             + ("  (no verdict — a live run, so nothing here was decided by a "
                "test)" if record.kind == "live" else ""),
             f"- **When:** {record.ts}"]
    if v:
        lines += [
            f"- **Outcome:** {v.get('outcome', '?')}"
            + (f" ({v['failure_class']})" if v.get("failure_class") else "")
            + f" · f2p {v.get('f2p_passed', '?')}/{v.get('f2p_total', '?')}"
              f" · p2p {v.get('p2p_passed', '?')}/{v.get('p2p_total', '?')}",
            f"- **Config:** {v.get('config', '?')} @ {str(v.get('config_sha', ''))[:12]}"
            f" · **Task:** {v.get('scenario', '?')}/{v.get('task', '?')}"
            f" ({v.get('difficulty', '?')})",
            f"- **Cost:** {v.get('provider_calls', '?')} calls, "
            f"{v.get('tokens_in', '?')} in / {v.get('tokens_out', '?')} out, "
            f"{v.get('steps', '?')} steps, {v.get('wall_time_s', '?')}s",
            f"- **Models:** {', '.join(v.get('models_used') or []) or '?'}"
            f" · bounces {v.get('failover_bounces', '?')}"
            f" · bad tool calls {v.get('bad_tool_calls', '?')}",
        ]
        for key in ("tampered_files", "lost_invariants", "broken_files"):
            if v.get(key):
                lines.append(f"- **{key}:** {', '.join(map(str, v[key]))}")

    lines.append(f"- **Files:** " + ", ".join(
        sorted(p.name for p in record.path.iterdir() if p.is_file())))

    events = records_mod.events(record)
    if events:
        tools = Counter(e.get("tool") for e in events
                        if e.get("event") == "tool_start")
        errors = [e for e in events if e.get("event") in ("tool_error", "llm_error")]
        lines += ["", "## What it did", "",
                  f"- Tools: " + (", ".join(f"{name}×{n}" for name, n
                                            in tools.most_common()) or "none"),
                  f"- Errors: {len(errors)}"]
        for event in errors[:5]:
            lines.append(f"  - {event.get('event')} "
                         f"{event.get('tool') or ''}: "
                         f"{str(event.get('detail'))[:200]}")
    else:
        lines += ["", "No `trace.jsonl`, so every trace-derived count above is "
                  "zero for instrumental reasons rather than because nothing "
                  "happened. Do not read one as cheapness."]

    gaps = records_mod.routing_gaps(record)
    if gaps:
        lines += ["", "## Longest silences between provider calls", "",
                  "*A long last gap means a call hung — the agent was not the "
                  "problem, and nothing else in this record distinguishes that "
                  "from bad judgement.*", ""]
        lines += [f"- {gap}" for gap in gaps]
    return "\n".join(lines)


def _turns(record) -> str:
    tree = records_mod.tree(record)
    turns = tree.get("turns") or []
    if not turns:
        return ("No `trace.json` in this record, so there is no turn-level "
                "history — it needs LANGSMITH_TRACING=1 and is fetched after "
                "the run, so a killed run never writes one. Use "
                "`section=\"events\"` for the flat timeline instead.")
    head = json.dumps(tree.get("run") or {}, indent=2)[:1200]
    rows = ["n   model                          ctx   tools"]
    for turn in turns:
        tools = ",".join(r.get("name", "?") for r in turn.get("tool_results") or [])
        rows.append(f"{str(turn.get('n', '?')):<4}{str(turn.get('model', '?')):<31}"
                    f"{str(turn.get('context_messages', '?')):<6}"
                    f"{'REWRITTEN ' if turn.get('context_rewritten') else ''}{tools}")
    return (f"# {record.id}\n\n```json\n{head}\n```\n\n"
            + "\n".join(rows)
            + "\n\n*`ctx` should climb. A drop, or REWRITTEN, dates a "
              "summarization — read that turn and the one before it to say what "
              "stopped being visible.*")


def _turn(record, which: str) -> str:
    turns = records_mod.tree(record).get("turns") or []
    if not turns:
        return "No `trace.json` in this record; there are no turns to read."
    try:
        wanted = int(which)
    except ValueError:
        return f"error: {which!r} is not a turn number."
    found = next((t for t in turns if t.get("n") == wanted), None)
    if found is None:
        return (f"error: this run has no turn {wanted}; it has "
                f"{len(turns)} turn(s).")

    # The tail of the context, not the whole of it: the question a review asks
    # is what was still visible, and the answer is at the end.
    history = found.get("input") or []
    lines = [f"# {record.id} — turn {wanted} ({found.get('model', '?')})", "",
             f"{len(history)} message(s) in context"
             + (" — REWRITTEN by summarization at this turn"
                if found.get("context_rewritten") else ""), "",
             "## The last of what it could see", ""]
    for message in history[-6:]:
        role = message.get("role", "?") if isinstance(message, dict) else "?"
        body = str(message.get("content", "") if isinstance(message, dict)
                   else message)
        lines.append(f"**{role}**: {body[:700]}")
    lines += ["", "## What it answered", "",
              str(found.get("output", ""))[:2000], "", "## What its tools returned", ""]
    for result in found.get("tool_results") or []:
        lines.append(f"- `{result.get('name', '?')}` "
                     f"[{result.get('status', '?')}]: "
                     f"{str(result.get('output', ''))[:600]}")
    return "\n".join(lines)


def _events(record) -> str:
    events = records_mod.events(record)
    if not events:
        return ("No `trace.jsonl` in this record. Nothing derived from it "
                "exists for this run, including every count in `summary`.")
    lines = []
    for event in events:
        kind = event.get("event")
        if kind == "tool_start":
            lines.append(f"tool  {event.get('tool')}({str(event.get('args'))[:180]})")
        elif kind == "tool_error":
            lines.append(f"  ERR {event.get('tool')}: {str(event.get('detail'))[:180]}")
        elif kind == "llm_error":
            lines.append(f"  REROUTE {str(event.get('detail'))[:180]}")
        elif kind == "llm_end":
            lines.append(f"llm   in={event.get('tokens_in')} "
                         f"out={event.get('tokens_out')}")
    return f"# {record.id} — {len(events)} event(s)\n\n" + "\n".join(lines)
