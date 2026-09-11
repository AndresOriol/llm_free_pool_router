"""The seven tools the improvement loop needs, and nothing else.

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
| `draft_scenario` | grow the instrument — a failed run becomes an eval case |

`draft_scenario` is the odd one out and earns its schema: the eval set is what
bounds every claim this loop can make, including about its own fixes, and five
scenarios cannot tell one configuration from another
([6.4.2](../../docs/06-agent.md#642-the-pass-column-is-noise)). A tool that
turns a failure into a test is the only one here that raises that ceiling.

Two shapes are deliberate and both are about the pool that serves this agent.

**Every parameter is a string or an int.** The members here are free-tier models
and a nested object schema is the thing they get wrong; a signature arrives as
JSON text and is parsed with an error the model can read and correct.

**Nothing returns a whole file.** `trace.json` is ~250 KB for a 17-turn run and
`read_file` on one would spend a session's context on a single run. Every
section below is bounded, and says what it clipped.

**What each tool is for, as the model reads it, is `tool_descriptions/<name>.md`.**
What a tool returns is written here, next to the code that computes it.
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

from agent import delegation
from agent.runtime import gitstate
from agent.runtime.prompts import fill
from agent.improve import issues as issues_mod
from agent.improve import records as records_mod
from agent.improve import repo
from agent.improve import scenarios as scenarios_mod

logger = logging.getLogger("harness.improve")

HERE = Path(__file__).parent

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


def describe(name: str) -> str:
    """What the model is told a tool is for: `tool_descriptions/<name>.md`."""
    return fill(HERE / "tool_descriptions" / f"{name}.md", {})


def _clip(text: str, limit: int = MAX_OUTPUT) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...(clipped, {len(text):,} chars total)"


def _tail(text: str, lines: int) -> str:
    return "\n".join((text or "").splitlines()[-lines:])


def make_tools(workdir: Path, peers=None) -> dict:
    """Build the tool set bound to one project. Returns name -> tool.

    `peers` names the agents this pass may run. Without `code` there is no
    `delegate_fix`, and the agent can diagnose but not fix -- which is a usable
    read-only mode and not an error, so the tool is absent rather than present
    and certain to refuse ([agent/delegation.py](../delegation.py)).
    """
    from langchain_core.tools import StructuredTool

    workdir = Path(workdir)
    store = issues_mod.IssueStore(workdir)
    drafts = scenarios_mod.DraftStore(workdir)

    def _records() -> list:
        return records_mod.discover(records_mod.default_roots(workdir))

    # -- detect ------------------------------------------------------------

    def find_runs(pattern: str = "", kind: str = "", since: str = "",
                  config: str = "", outcome: str = "", limit: int = 20) -> str:
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
        return (f"Wrote `{store.path(issue.id)}`.\n\n{_staleness(issue)}"
                f"{issue.render()}")

    def _staleness(issue) -> str:
        """A warning when no run matching this issue ran the current lever.

        This is the check the first live pass needed and did not have. It opened
        a well-formed, correctly cited issue about a 120-step recursion limit
        that had been 400 for a day, and spent a delegation on it. The evidence
        was real and the conclusion was stale, and the difference between them
        is one question about the file it had already named as the lever.

        The question is *which commit each run exercised*, not when it ran: an
        eval run pins a SHA, so a run started after a fix can still be running
        the code from before it ([records.saw_current_lever](records.py)).
        """
        if not issue.signature or not issue.lever:
            return ""
        matching = [r for r in _records()
                    if records_mod.matches(r, issue.signature)]
        if not records_mod.evidence_predates_lever(workdir, issue.lever, matching):
            return ""
        changed = records_mod.lever_changed_at(workdir, issue.lever) or "?"
        return (f"⚠ **This may already be fixed.** `{issue.lever}` last changed "
                f"at {changed[:19]}, and not one of the {len(matching)} run(s) "
                f"matching this signature ran the file as it now stands — each "
                f"either pinned a commit without that change or predates it. "
                f"Every run you are citing is about a different version of the "
                f"code you want changed.\n\nRead the lever and its recent "
                f"history (`git log -p -n 3 {issue.lever.lstrip('/')}`) before "
                f"you delegate anything. If the fix is already there, say so "
                f"and close this issue — do not spend a coding session "
                f"re-making a change that exists.\n\n")

    # -- fix ---------------------------------------------------------------

    def delegate_fix(issue_id: str, brief: str) -> str:
        issue = store.get(issue_id)
        if issue is None:
            return (f"error: no issue called {issue_id!r}. Open one with "
                    f"`write_issue` first — a fix with no issue cannot be "
                    f"verified afterwards.")
        if not brief.strip():
            return "error: the brief is empty."

        # Refused here, only warned in `write_issue`. A lever may have changed
        # for an unrelated reason and only a reader can tell, so naming an
        # issue stays cheap -- but a delegation spends a whole coding session,
        # and the rule at that price is strict: **you may not ask for a change
        # to a file when the failure has never been observed against the
        # current state of that file.**
        #
        # The way through is the correct behaviour and not a workaround: record
        # a run with `run_evals` and add it as evidence. If the failure is real
        # it will still be there, and the issue is then about the code as it
        # actually is.
        stale = _staleness(issue)
        if stale:
            return (stale + "**Nothing was delegated.** Record a run against "
                    "the current code with `run_evals` and add it to this "
                    "issue's evidence. If the failure is still there, the "
                    "issue is real and this will let it through; if it is not, "
                    "the fix already shipped and the issue should close.")

        # One branch per issue, taken from wherever the pass started. The first
        # live pass delegated on `master` and nothing prevented it; the only
        # reason master was not written to is that the session made no change
        # ([repo.py](repo.py)).
        branch = repo.branch_name(issue.id)
        if repo.is_repo(workdir):
            ok, note = repo.commit_ledger(workdir, issue.id)
            if not ok:
                return (f"error: could not put the ledger on this branch: "
                        f"{note}. Nothing was delegated — a fix branch that "
                        f"carried the diagnosis away is the bug this "
                        f"prevents.")
            ok, note = repo.switch_to(workdir, branch)
            if not ok:
                return (f"error: could not put the work on `{branch}`: {note}. "
                        f"Nothing was delegated — a fix committed onto whatever "
                        f"branch happens to be checked out is not reviewable.")
            logger.info(f"Delegating {issue.id} {note}")
        else:
            branch = ""

        request = (f"{brief.strip()}\n\n"
                   f"---\nThis addresses issue `{issue.id}` — {issue.title}. "
                   f"The diagnosis behind it is in "
                   f"`{issues_mod.ISSUES_DIR.as_posix()}/{issue.id}.json`."
                   + (f"\n\nYou are on branch `{branch}`, which exists for this "
                      f"issue. Commit here and do not switch branches."
                      if branch else ""))
        before = gitstate.head(workdir)
        code, output = delegation.run("code", workdir, request)
        report = gitstate.state(workdir, before)

        # What the repository says, read from git rather than out of the
        # delegate's prose. The first delegation this loop ever made came back
        # describing three changes to `session.py` that the diff did not
        # contain (docs/19-improvement-agent.md#199-what-the-first-live-pass-showed).
        moved = gitstate.moved(report)
        rendered = _delegation(code, output, report)

        # The suite, after any delegation that moved the repository, and never
        # as a tool the agent could forget to reach for. A change that breaks
        # the tests is not a fix, whatever its signature does afterwards.
        suite_passed, suite_tail = (True, "")
        if moved:
            suite_passed, suite_tail = repo.run_tests(
                workdir, int(os.environ.get(TIMEOUT_ENV) or DEFAULT_TIMEOUT))

        issue.tasks.append({"ts": issues_mod.now(), "agent": "code",
                            "exit_code": code,
                            "changed_anything": moved, "branch": branch,
                            "tests_passed": suite_passed,
                            "brief": brief.strip()[:1000]})
        if moved and not suite_passed:
            issue.note("regressed", "the delegated fix broke the suite")
            store.save(issue)
            return (
                f"{rendered}\n\n**The change broke this project's "
                f"own test suite, so it is not a fix.** It is committed on "
                f"`{branch}` and nothing has been merged, so nothing else is "
                f"affected — but `{issue.id}` stays {issue.status} and must not "
                f"be verified or closed on this.\n\n```\n{_tail(suite_tail, 15)}"
                f"\n```\n\nEither delegate a follow-up naming these failures, "
                f"or say on the issue that the fix could not be made without "
                f"breaking them. Do not run `run_evals` against this branch.")

        if moved and issue.status in (issues_mod.OPEN, issues_mod.REOPENED):
            issue.note("status", f"{issue.status} -> {issues_mod.FIXING}")
            issue.status = issues_mod.FIXING
        issue.note("delegated",
                   f"ran code; repository "
                   f"{'moved' if moved else 'did not move'}")
        store.save(issue)

        if not moved:
            # The status deliberately does not advance. A delegation that
            # changed nothing is not the first half of a fix, and recording it
            # as `fixing` would leave the ledger claiming work is under way
            # when the repository says none was done.
            return (
                f"{rendered}\n\n**The delegation changed nothing, "
                f"so `{issue.id}` is still {issue.status}.** Git reports "
                f"no commit and no files changed, whatever its closing message "
                f"says — the first delegation this loop ever made came back "
                f"describing three changes to `session.py` that the diff did "
                f"not contain.\n\nTreat that as a finding about this issue: "
                f"either the fix was already present (check the lever, and "
                f"close this if so), or the brief did not say enough to act "
                f"on. Do not delegate the same brief again.")

        return (
            f"{rendered}\n\nRecorded against `{issue.id}`, now "
            f"**{issue.status}**. The suite still passes.\n\n"
            f"The repository moved, which is more than the closing message "
            f"above is worth on its own — read the diff before you believe "
            f"what it changed. It is not fixed until runs recorded after this "
            f"stop matching the signature: `run_evals` on the scenario the "
            f"issue was seen in with `ref=\"{branch}\"`, then `check_issue`. "
            f"**Passing `ref` is not optional** — the fix is on that branch and "
            f"a configuration pins `master`, so without it you would measure "
            f"the unfixed code and conclude the fix failed.")

    # -- grow the instrument -----------------------------------------------

    def draft_scenario(title: str, prompt: str, fail_to_pass: str,
                       source_run: str = "", id: str = "", seed: str = "",
                       challenge: str = "", pass_to_pass: str = "",
                       traps: str = "", invariants: str = "",
                       category: str = "bugfix", difficulty: str = "L1",
                       archetype: str = "", build: str = "") -> str:
        record = (records_mod.load(workdir, source_run) if source_run else None)
        if source_run and record is None:
            return (f"error: no run called {source_run!r}, so the draft would "
                    f"have no provenance. Use `find_runs`, or leave "
                    f"`source_run` empty and say in `challenge` where this "
                    f"came from.")
        try:
            made = scenarios_mod.draft(drafts, {
                "id": id, "title": title, "prompt": prompt,
                "fail_to_pass": fail_to_pass, "pass_to_pass": pass_to_pass,
                "seed": seed, "challenge": challenge, "traps": traps,
                "invariants": invariants, "category": category,
                "difficulty": difficulty, "archetype": archetype,
                "source_run": source_run}, record)
        except (ValueError, OSError) as exc:
            return f"error: {exc}. Nothing written."

        written = f"Wrote `{drafts.path(made.id)}`.\n\n{made.render()}"
        if str(build).strip().lower() not in ("yes", "true", "1"):
            return _clip(
                written + "\n\n---\n\nNothing has been built. Read this back, "
                "and call again with `build=\"yes\"` when it is right — or "
                "leave it as a brief for a human, which is a complete result.")

        if "scenarios" not in (peers or ()):
            return _clip(
                written + "\n\n---\n\n**It cannot be built from here.** No "
                "`scenarios` agent is reachable, which means the scenario "
                "repository was not found. The draft is on disk and a coding "
                "session pointed at that repository can build it from the file.")

        # The same coding agent, run against the scenario repository instead of
        # this one, so the before-shot is taken there too.
        repo = delegation.scenario_repo()
        before = gitstate.head(repo)
        code, output = delegation.run(
            "scenarios", workdir,
            scenarios_mod.builder_brief(made, "the eval scenario repository"))
        built = _delegation(code, output, gitstate.state(repo, before))
        return _clip(
            f"{written}\n\n---\n\n{built}\n\n"
            f"**A scenario is not built until its gate passes.** Run "
            f"`python -m evals validate --scenario <tag>`: it checks that the "
            f"untouched seed fails `fail_to_pass`, that the gold patch makes it "
            f"pass, and that nothing under `evaluation/` leaked into the "
            f"workdir. Until then this is a directory, not an instrument.")

    # -- make new evidence -------------------------------------------------

    def run_evals(config: str = "code", scenario: str = "", reps: int = 1,
                  ref: str = "", topic: str = "") -> str:
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
            func=find_runs, name="find_runs", description=describe("find_runs")),
        "read_run": StructuredTool.from_function(
            func=read_run, name="read_run", description=describe("read_run")),
        "write_issue": StructuredTool.from_function(
            func=write_issue, name="write_issue", description=describe("write_issue")),
        "run_evals": StructuredTool.from_function(
            func=run_evals, name="run_evals", description=describe("run_evals")),
        "check_issue": StructuredTool.from_function(
            func=check_issue, name="check_issue", description=describe("check_issue")),
        "draft_scenario": StructuredTool.from_function(
            func=draft_scenario, name="draft_scenario",
            description=describe("draft_scenario")),
    }
    if "code" in (peers or ()):
        made["delegate_fix"] = StructuredTool.from_function(
            func=delegate_fix, name="delegate_fix",
            description=describe("delegate_fix"))
    else:
        logger.warning("No `code` peer is reachable; this pass can diagnose "
                       "but not fix.")
    return made


def _delegation(code, output: str, report: dict) -> str:
    """What a delegated session did, as the text the pass reads back.

    Verdict first, then the session's own account of itself. The order is the
    point: a caller reading "no commit, and nothing changed on disk" cannot
    accept "I made three changes" from the prose underneath it
    ([gitstate.render](../runtime/gitstate.py)).

    A timeout (`code is None`) is not a crash. The session did real work and was
    stopped; whatever it committed is still committed, and the git report below
    is what says so.
    """
    if code is None:
        head = ("The session hit the delegation timeout and was stopped. "
                "Whatever it had committed is still committed:")
    elif code != 0:
        head = f"`python -m agent.code` exited {code}:"
    else:
        head = "The coding session finished."

    verdict = gitstate.render(report)
    said = delegation.tail(output, 40)
    parts = [head, verdict, f"--- what it said (tail) ---\n{said}"]
    return "\n\n".join(p for p in parts if p)


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
