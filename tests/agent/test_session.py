"""Checks for the session harness: the envelope, the journal, git, the rationale.

This is the *fast tier* described in docs/design/long-run-harness.md#6-how-this-
gets-tested: it tests machinery, never intelligence. Every check here is
deterministic and spends no pool quota, so it can gate every commit -- and so a
failure means the harness is broken rather than that a model had a bad day.

    python -m pytest tests/agent/test_session.py
"""

import json
import tempfile
from pathlib import Path

from agent.harness.log import Log
from agent.harness.protocol import Brief, parse_brief, parse_report
from agent.harness.gate import Suite
from agent.harness.graph import _veto, mermaid
from agent.harness.session import run_session
from agent.harness.record import (Git, Journal, Step, Transcript,
                                  append_to_notes, read_notes, replay,
                                  write_rationale)
from agent.harness.nodes import NodeResult, ORCHESTRATOR, Stats, WORKERS, run_node
from agent.runtime.tools import make_tools
from agent.runtime.backend import RestrictedShellBackend
from tests.agent.test_harness import FIXED, ScriptedModel, ai, check, seed_project


# --- the envelope ----------------------------------------------------------

def test_brief_parsing():
    brief = parse_brief(
        "ACTION: WRITE\n"
        "GOAL: make parse_retry_after case-insensitive\n"
        "CONTEXT: retry.py line 4 reads headers.get('retry-after')\n"
        "DONE_WHEN: the lookup no longer depends on the header's case")
    check("action", brief.action == "WRITE")
    check("goal", brief.goal.startswith("make parse_retry_after"))
    check("context carries the fact", "retry.py line 4" in brief.context)
    check("done_when", "case" in brief.done_when)


def test_brief_survives_a_chatty_orchestrator():
    """A model that writes prose instead of the form must not end a session."""
    brief = parse_brief("Sure! I think the right move here is to EXPLORE the "
                        "repo a bit more before we touch anything.")
    check("action still found", brief.action == "EXPLORE")

    blank = parse_brief("hmm")
    check("unparseable falls back to the read-only role", blank.action == "EXPLORE")

    partial = parse_brief("ACTION: REVIEW\nGOAL: check it")
    check("missing fields are empty, not fatal", partial.done_when == "")


def test_labels_parse_when_they_share_a_line():
    """Observed on the first live run: models write the whole reply on one line.

    Requiring a line start swallowed the status into the finding, so every
    finding in the journal began with the literal text `STATUS: DONE | FINDING:`.
    """
    report = parse_report("STATUS: DONE | FINDING: fixed the parser")
    check("status", report.status == "DONE")
    check("finding is clean", report.finding == "fixed the parser")

    brief = parse_brief("ACTION: WRITE | GOAL: fix it | CONTEXT: line 4 | "
                        "DONE_WHEN: the edit applies")
    check("action", brief.action == "WRITE")
    check("goal is clean", brief.goal == "fix it")
    check("context is clean", brief.context == "line 4")


def test_the_writer_can_read():
    """`replace_in_file` needs an exact `old_text`. A writer that cannot look
    reported BLOCKED with 'no tool for reading files is provided'."""
    check("write can read", "read_lines" in WORKERS["write"].tools)
    check("but still cannot search", "search_code" not in WORKERS["write"].tools)


def test_report_never_invents_success():
    """An unparseable reply is PARTIAL. Only an explicit DONE is DONE."""
    check("no status line", parse_report("I did some stuff").status == "PARTIAL")
    check("explicit", parse_report("STATUS: DONE\nFINDING: fixed").status == "DONE")
    check("finding kept", parse_report("STATUS: DONE\nFINDING: fixed").finding == "fixed")
    # "blocked" in prose is not a status; only the STATUS line decides.
    check("prose does not set status",
          parse_report("STATUS: DONE\nFINDING: nothing blocked me").status == "DONE")
    check("asking for context",
          parse_report("STATUS: INSUFFICIENT_CONTEXT\nFINDING: which file?"
                       ).status == "INSUFFICIENT_CONTEXT")


# --- verdicts come from effects, not from prose ----------------------------

def test_execute_status_comes_from_the_exit_code():
    """The load-bearing rule: a model claiming success about a failing run must
    not be able to end a session, least of all under prose-only review."""
    result = NodeResult(
        text="Everything passes, we are done!",
        outputs=[("run_command", "exit=1\nE   assert 1 == 3")],
        calls=[{"name": "run_command", "args": {"command": "python -m pytest"},
                "output": "exit=1\nE   assert 1 == 3"}])
    report = WORKERS["execute"].report(result)
    check("failure is not DONE", report.status != "DONE")
    check("the command is on the record",
          report.evidence == [["python -m pytest", 1]])

    ok = NodeResult(text="", outputs=[("run_command", "exit=0\n2 passed")],
                    calls=[{"name": "run_command", "args": {"command": "python -m pytest"},
                            "output": "exit=0\n2 passed"}])
    check("success is DONE", WORKERS["execute"].report(ok).status == "DONE")


def test_writer_is_judged_by_what_it_applied():
    described = NodeResult(text="I would change line 4 to lowercase the key.",
                           outputs=[], calls=[])
    check("describing an edit is not making one",
          WORKERS["write"].report(described).status == "PARTIAL")

    applied = NodeResult(text="", outputs=[("replace_in_file", "ok: replaced 1 in /r.py")],
                         calls=[])
    check("an applied edit is DONE", WORKERS["write"].report(applied).status == "DONE")


def test_a_role_can_refuse_to_guess():
    """The return path that turns a wasted cycle into an informative one."""
    result = NodeResult(text="STATUS: INSUFFICIENT_CONTEXT\nFINDING: no file named",
                        outputs=[], calls=[])
    report = WORKERS["write"].report(result)
    check("status survives from an acting role", report.needs_context)
    check("what is missing is carried up", "no file named" in report.finding)

    # ...but it must never overwrite a demonstrated success.
    did_both = NodeResult(text="INSUFFICIENT_CONTEXT",
                          outputs=[("replace_in_file", "ok: replaced 1 in /r.py")], calls=[])
    check("evidence of success wins", WORKERS["write"].report(did_both).status == "DONE")


# --- git -------------------------------------------------------------------

def test_git_allowlist_refuses_what_the_human_gate_needs():
    root = Path(tempfile.mkdtemp())
    backend = RestrictedShellBackend(root_dir=str(root), allow_git=True)

    for command in ("git status", "git diff", "git add -A", "git log",
                    "git commit -m hello", "git checkout -b session/x"):
        check(f"{command!r} must be allowed", backend._refusal(command.split()) == "")

    for command in ("git push", "git merge main", "git rebase main",
                    "git reset --hard HEAD", "git clean -fd", "git checkout main",
                    "git branch -D main", "git remote add x y"):
        check(f"{command!r} must be refused", backend._refusal(command.split()) != "")

    check("git is absent unless asked for",
          RestrictedShellBackend(root_dir=str(root))._refusal(["git", "status"]) != "")


def test_session_branches_and_commits():
    root = seed_project()
    git = Git(root)
    check("a plain directory becomes a repo", git.start("test1"))
    check("on its own branch", git.branch == "session/test1")

    (root / "calc.py").write_text(FIXED)
    check("commits the change", git.commit("fix add"))
    check("nothing to commit is not an error", git.commit("again") is False)
    check("the diff is the session's own work", "return a + b" in git.diff())


# --- crash resume ----------------------------------------------------------

def test_journal_replays_knowledge_not_actions():
    root = Path(tempfile.mkdtemp())
    journal = Journal(root / "journal.jsonl")
    journal.append(Step(n=1, action="explore", goal="find it",
                        status="DONE", finding="the bug is in /retry.py"))
    journal.append(Step(n=2, action="execute", goal="check", status="PARTIAL",
                        finding="one test fails",
                        evidence=[["python -m pytest", 1]]))
    # A crash mid-write leaves a partial line; it must be skipped, not fatal.
    with (root / "journal.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"n": 3, "action": "wri')

    log = Log(task="t")
    replayed = replay(journal, log)
    check("only completed steps are replayed", replayed == 2)
    check("findings are restored", any("/retry.py" in n for n in log.texts("notes")))
    check("evidence is restored",
          any("exit 1" in line for line in log.texts("steps")))
    check("both findings carry over", len(log.texts("notes")) == 2)


# --- the human interface ---------------------------------------------------

def test_notes_in_notes_out():
    root = Path(tempfile.mkdtemp())
    (root / "NOTES.md").write_text("# Notes\n\n- The retry helper is case-sensitive.\n")
    check("notes are read", "case-sensitive" in read_notes(root))

    append_to_notes(root, "s1", "done", Path(".harness/reports/session-s1.md"),
                    "2 steps, 1 change.")
    text = (root / "NOTES.md").read_text()
    check("the original survives", "case-sensitive" in text)
    check("the session appends its account", "Session s1" in text)


def test_rationale_is_built_from_the_journal():
    root = Path(tempfile.mkdtemp())
    steps = [
        {"n": 1, "action": "write", "status": "DONE", "finding": "ok: replaced 1 in /r.py",
         "evidence": []},
        {"n": 2, "action": "execute", "status": "DONE", "finding": "ran 1 command",
         "evidence": [["python -m pytest", 0]]},
    ]
    text = write_rationale(root / "r.md", "s1", "fix the header lookup", steps,
                           "done", "+++ b/r.py\n", "session/s1")
    check("the outcome is stated", "done" in text)
    # Reported 0 for every session ever written, because the pattern lived in an
    # f-string and its escaping was doubled.
    check("changed files are counted", "**Files changed:** 1" in text)
    check("commands are cited with their exit codes", "`python -m pytest` → exit 0" in text)
    check("no open questions when there are none", "None recorded" in text)

    stuck = write_rationale(root / "r2.md", "s2", "t",
                            [{"n": 1, "action": "write", "status": "INSUFFICIENT_CONTEXT",
                              "finding": "which file holds the parser?", "evidence": []}],
                            "exhausted", "", "session/s2")
    # R3: nobody is watching until morning, so being stuck is written down.
    check("being stuck is reported, not swallowed",
          "which file holds the parser?" in stuck)
    check("an unexecuted session says so", "Nothing was executed" in stuck)


# --- context tiers ---------------------------------------------------------

def test_judgement_roles_declare_a_context_floor():
    check("the orchestrator cannot decide on a narrow view",
          ORCHESTRATOR.min_context > 12_000)
    for name in ("document", "review"):
        check(f"{name} needs breadth", WORKERS[name].min_context > 12_000)
    for name in ("explore", "write", "execute"):
        check(f"{name} runs anywhere", WORKERS[name].min_context == 0)


def test_narrow_roles_still_fit_the_smallest_member():
    """The budget claim, per tier: the roles that run anywhere must still fit
    the 6,000-TPM member even on a saturated log."""
    root = seed_project()
    backend = RestrictedShellBackend(root_dir=str(root))
    toolset = make_tools(backend)
    log = Log(task="fix the retry helper")
    for i in range(30):
        log.note("explore", f"finding number {i} " + "y" * 400)
        log.files("explore", [f"/mod{i}.py"])
        log.edit("write", f"ok: edit {i}")
    log.ran("execute", "E   assert 1 == 3\n" * 400, False)
    log.diff("+++ b/retry.py\n" + "+    x = 1\n" * 400)
    brief = Brief(action="WRITE", goal="g" * 300, context="c" * 1_200,
                  done_when="d" * 300)

    worst = 0
    for name in ("explore", "write", "execute"):
        stats = Stats()
        run_node(ScriptedModel([ai("ok")]), WORKERS[name], toolset, log, {},
                 stats, brief=brief)
        worst = max(worst, stats.prompt_tokens)
    check(f"worst narrow-role prompt {worst} tok must fit a 6k-TPM model",
          worst < 5_400)
    return worst


# --- the loop --------------------------------------------------------------

def test_the_graph_is_the_shape_we_document():
    """The graph draws itself, so this pins the shape the docs describe.

    Before this was a StateGraph the topology lived in a `while` and a string
    variable, and the only picture of it was an ASCII drawing in a docstring
    that nothing checked.
    """
    picture = mermaid()
    for role in WORKERS:
        check(f"{role} is a node", f"{role}(" in picture)

    # Every worker returns to the hub, so the orchestrator decides every step.
    for role in WORKERS:
        if role != "write":
            check(f"{role} returns to the orchestrator",
                  f"{role} --> orchestrate;" in picture)

    # Except a write, which goes straight to running the code when it landed.
    check("an applied edit runs the code without asking",
          "write -.-> execute;" in picture)
    check("and asks again when it did not",
          "write -.-> orchestrate;" in picture)
    check("only the orchestrator can end the run", "orchestrate -.-> __end__;" in picture)


def test_the_budget_counts_steps_not_supersteps():
    """LangGraph's recursion_limit counts supersteps; the scenario timeout and
    every cost figure are reasoned about in journal steps. The budget stays a
    step budget, and recursion_limit is only the backstop."""
    root = seed_project()
    backend = RestrictedShellBackend(root_dir=str(root), allow_git=True)
    toolset = make_tools(backend)
    # An orchestrator that only ever explores, and an explorer that finds
    # nothing: the run has no way to end except the budget.
    model = ScriptedModel([
        ai("ACTION: EXPLORE|GOAL: look|CONTEXT: |DONE_WHEN: x"),
        ai("STATUS: PARTIAL | FINDING: nothing yet"),
    ] * 20)

    log, stats, outcome, steps = run_session(model, toolset, "fix it",
                                            root, max_steps=3)
    check(f"outcome was {outcome}", outcome == "exhausted")
    check(f"exactly the budget in journal steps, got {len(steps)}", len(steps) == 3)
    check("and it still wrote its rationale",
          list((root / ".harness" / "reports").glob("session-*.md")))


def test_session_end_to_end():
    """write -> (auto) execute -> DONE refused -> review -> DONE."""
    root = seed_project()
    backend = RestrictedShellBackend(root_dir=str(root), allow_git=True)
    toolset = make_tools(backend)
    model = ScriptedModel([
        ai("ACTION: WRITE\nGOAL: fix add\nCONTEXT: /calc.py returns a - b\n"
           "DONE_WHEN: the edit is applied"),
        ai(calls=[("replace_in_file", {"file_path": "/calc.py",
                                       "old_text": "return a - b",
                                       "new_text": "return a + b"})]),
        ai("applied"),
        # The forced execute step: no orchestrator call is spent on it.
        ai(calls=[("run_command", {"command": "python -m pytest"})]),
        ai("passed"),
        ai("ACTION: DONE"),                      # refused: nothing reviewed yet
        ai("STATUS: DONE\nFINDING: the change is correct"),
        ai("ACTION: DONE"),                      # accepted
    ])

    log, stats, outcome, steps = run_session(model, toolset,
                                            "add() is wrong", root, max_steps=8)

    check(f"outcome was {outcome}", outcome == "done")
    check("the file is actually fixed", (root / "calc.py").read_text() == FIXED)
    check("the run verified itself", log.exec_ok is True)
    check("an unreviewed done was refused",
          any("nothing has been reviewed" in line for line in log.texts("steps")))
    check("the edit was followed by a run without asking",
          [s["action"] for s in steps][:2] == ["write", "execute"])
    check("every step is journalled", len(steps) == 3)
    check("the rationale exists",
          (root / ".harness" / "reports").glob("session-*.md"))
    check("the notes carry the account", "Session" in (root / "NOTES.md").read_text())
    check("the orchestrator held no tools",
          any(s["tools"] is None for s in model.seen))
    return stats


def test_endless_exploration_is_refused():
    """Nine of twelve steps on the first live run were `explore`, re-reading the
    same files. Reading is the move an orchestrator can always justify."""
    root = seed_project()
    backend = RestrictedShellBackend(root_dir=str(root), allow_git=True)
    toolset = make_tools(backend)
    explore_forever = [
        ai("ACTION: EXPLORE\nGOAL: look again\nCONTEXT: \nDONE_WHEN: understood"),
        ai(calls=[("search_code", {"pattern": "def add"})]),
        ai("STATUS: DONE\nFINDING: /calc.py returns a - b"),
    ]
    model = ScriptedModel(explore_forever * 2 + [
        # The third EXPLORE is overridden, so the next call is the writer's.
        ai("ACTION: EXPLORE\nGOAL: look yet again\nCONTEXT: \nDONE_WHEN: x"),
        ai(calls=[("replace_in_file", {"file_path": "/calc.py",
                                       "old_text": "return a - b",
                                       "new_text": "return a + b"})]),
        ai("applied"),
    ] + [ai("ACTION: GIVEUP")] * 6)

    log, stats, outcome, steps = run_session(model, toolset,
                                            "add() is wrong", root, max_steps=6)
    actions = [s["action"] for s in steps]
    check(f"a write was forced, got {actions}", "write" in actions)
    check("and it is recorded as a refusal",
          any("EXPLORE refused" in line for line in log.texts("steps")))


def test_every_turn_records_what_it_was_given():
    """The evidence a post-mortem needs, and did not have.

    A run's journal said `write: INSUFFICIENT_CONTEXT: missing the contents of
    alerts/rules.py` and nothing on disk said what the writer had been handed.
    That is the difference between naming a failure and diagnosing it, so the
    prompt and the raw reply are now kept per turn.
    """
    root = seed_project()
    backend = RestrictedShellBackend(root_dir=str(root), allow_git=True)
    toolset = make_tools(backend)
    model = ScriptedModel([
        ai("ACTION: WRITE\nGOAL: fix add\nCONTEXT: /calc.py returns a - b\n"
           "DONE_WHEN: the edit is applied"),
        ai(calls=[("replace_in_file", {"file_path": "/calc.py",
                                       "old_text": "return a - b",
                                       "new_text": "return a + b"})]),
        ai("STATUS: DONE | FINDING: applied"),
    ] + [ai("ACTION: GIVEUP")] * 4)

    log, stats, outcome, steps = run_session(model, toolset,
                                            "add() is wrong", root, max_steps=2)

    turns = sorted((root / ".harness" / "steps").glob("*.md"))
    check(f"one file per model turn, got {[t.name for t in turns]}", len(turns) >= 2)
    check("the orchestrator's own turn is kept too",
          turns[0].name.endswith("-orchestrate.md"))

    write_turn = next(t for t in turns if t.name.endswith("-write.md"))
    text = write_turn.read_text(encoding="utf-8")
    check("the brief it was given is recorded", "/calc.py returns a - b" in text)
    check("so is the prompt it actually received", "# Task" in text)
    check("and the reply before parsing mangled it", "STATUS: DONE | FINDING" in text)
    check("with the tool calls it made", "replace_in_file" in text)

    journaled = [s for s in steps if s["action"] == "write"][0]
    check("the journal carries the whole brief, not just the goal",
          journaled["context"] == "/calc.py returns a - b")
    check("including what would have ended the step",
          journaled["done_when"] == "the edit is applied")


def test_the_session_keeps_its_own_bookkeeping_out_of_the_diff():
    """The diff is what the orchestrator, documenter and reviewer are shown as
    *the change made so far*, head-clipped at 6,000 characters.

    A recorded run's diff section was 100% `.harness/` -- the journal and the
    per-turn transcript -- and clipped out before reaching the one edited source
    file. The orchestrator read its own transcript where the code should have
    been.
    """
    root = seed_project()
    backend = RestrictedShellBackend(root_dir=str(root), allow_git=True)
    toolset = make_tools(backend)
    model = ScriptedModel([
        ai("ACTION: WRITE\nGOAL: fix add\nCONTEXT: /calc.py\nDONE_WHEN: applied"),
        ai(calls=[("replace_in_file", {"file_path": "/calc.py",
                                       "old_text": "return a - b",
                                       "new_text": "return a + b"})]),
        ai("STATUS: DONE | FINDING: applied"),
    ] + [ai("ACTION: GIVEUP")] * 4)

    log, stats, outcome, steps = run_session(model, toolset,
                                            "add() is wrong", root, max_steps=2)

    check("the transcript was still written",
          list((root / ".harness" / "steps").glob("*.md")))
    diff = log.texts("diff")[-1]
    check(f"and it is nowhere in the diff:\n{diff[:300]}", ".harness" not in diff)
    check("while the real change is", "calc.py" in diff)


def test_a_resumed_session_does_not_overwrite_its_transcript():
    """R2 says a killed session resumes. Turn 1 of the second life must not
    land on top of turn 1 of the first, or the crash erases its own cause."""
    root = seed_project()
    directory = root / ".harness" / "steps"
    first = Transcript(directory)
    first.write("explore", NodeResult(text="a", outputs=[], prompt="p"))
    resumed = Transcript(directory)
    resumed.write("write", NodeResult(text="b", outputs=[], prompt="q"))
    check("both turns survive", len(list(directory.glob("*.md"))) == 2)
    check("and the numbering continued", (directory / "02-write.md").is_file())


def test_a_session_that_gets_stuck_still_reports():
    """R3: it never blocks on a human and never ends silently."""
    root = seed_project()
    backend = RestrictedShellBackend(root_dir=str(root), allow_git=True)
    toolset = make_tools(backend)
    model = ScriptedModel([
        ai("ACTION: WRITE\nGOAL: fix it\nCONTEXT: \nDONE_WHEN: applied"),
        ai("STATUS: INSUFFICIENT_CONTEXT\nFINDING: no file was named"),
        ai("ACTION: GIVEUP"),
    ])
    log, stats, outcome, steps = run_session(model, toolset, "fix it",
                                            root, max_steps=6)
    check(f"outcome was {outcome}", outcome == "giveup")
    rationale = next((root / ".harness" / "reports").glob("session-*.md")).read_text()
    check("the open question is written down", "no file was named" in rationale)
    check("the notes were still updated", (root / "NOTES.md").is_file())


# --- the regression gate ---------------------------------------------------


class FakeSuite:
    """Stands in for the agent's `run_command` tool, over a scripted suite.

    A snapshot is two commands -- collect, then run -- so this answers by which
    one it was asked, and `advance()` moves it to the next state of the world.
    """

    def __init__(self, *states):
        # Each state is (collected_ids, failing_ids).
        self.states = list(states)
        self.at = 0

    def advance(self):
        self.at = min(self.at + 1, len(self.states) - 1)

    def invoke(self, args):
        collected, failing = self.states[self.at]
        if "--collect-only" in args["command"]:
            return "exit=0\n" + "\n".join(collected) + \
                   f"\n\n{len(collected)} tests collected in 0.06s"
        lines = [f"exit={1 if failing else 0}"]
        lines += [f"FAILED {name} - AssertionError" for name in failing]
        lines.append(f"{len(failing)} failed, {len(collected) - len(failing)} passed")
        # The gate re-reads the world every time it is asked, so a second
        # snapshot in one test means the state has moved on.
        self.advance()
        return "\n".join(lines)


A, B, C = ("t.py::test_a", "t.py::test_b", "t.py::test_c")


def test_gate_baseline_is_what_passes():
    """Not what fails: only this reading notices a test that stops existing."""
    gate = Suite(FakeSuite(([A, B, C], [C])))
    check("the passing tests are the baseline",
          gate.take_baseline() == frozenset({A, B}))


def test_a_test_that_was_already_failing_is_not_a_regression():
    """The scenario that opens red is the normal case. Demanding green would
    refuse the very fix the session was asked to make."""
    gate = Suite(FakeSuite(([A, B], [B]), ([A, B], [B])))
    gate.take_baseline()
    check("still-failing is not new", gate.regressions() == [])


def test_a_newly_broken_test_is_a_regression():
    gate = Suite(FakeSuite(([A, B], [B]), ([A, B], [A, B])))
    gate.take_baseline()
    check("the new failure is named", gate.regressions() == [A])


def test_a_test_edited_out_of_the_way_is_a_regression():
    """Measured on this gate's own first run: told to set a field the suite
    forbids, the session deleted the assertion and renamed the test around it.
    Nothing fails afterwards, so comparing failures sees a clean run."""
    gate = Suite(FakeSuite(([A, B], []), ([A, "t.py::test_b_renamed"], [])))
    gate.take_baseline()
    check("a test that stopped existing is a regression",
          gate.regressions() == [B])


def test_a_suite_that_cannot_run_leaves_the_gate_quiet():
    """A gate that cannot see the tests must not have an opinion about them."""

    class Broken:
        def __init__(self, output):
            self.output = output

        def invoke(self, args):
            return self.output

    for output in ("exit=4\nERROR: file or directory not found: tests",
                   "exit=5\nno tests ran in 0.01s",
                   "exit=2\nINTERNALERROR"):
        gate = Suite(Broken(output))
        check(f"unusable suite is not a baseline ({output[:6]})",
              gate.take_baseline() is None)
        check("and blocks nothing", gate.regressions() == [])


def test_done_is_refused_when_the_session_broke_something():
    """The refusal the harness could not make before: it had no idea what used
    to work. Observed on scenario/ledger/count-and-share, where the notes ask
    for a change the project's own tests forbid."""
    log = Log(task="add a count to the summary")
    log.add("exec", "execute", "(SUCCEEDED)", ok=True)

    gate = Suite(FakeSuite(([A, B], []), ([A, B], [B])))
    gate.take_baseline()
    brief, action = _veto(Brief(action="DONE"), {"reviewed": True}, log, gate)
    check("DONE is refused", action == "write")
    check("the broken test is named in the brief", B in brief.context)
    check("and the writer is told not to edit the test away",
          "editing the tests" in brief.done_when)

    # Same session, nothing broken: the gate gets out of the way.
    clean = Suite(FakeSuite(([A, B], []), ([A, B], [])))
    clean.take_baseline()
    _, ok_action = _veto(Brief(action="DONE"), {"reviewed": True}, log, clean)
    check("a clean run still finishes", ok_action == "done")


def test_the_writer_is_told_tests_are_not_its_to_edit():
    """It can now see the test that contradicts its brief, so it needs the rule
    that keeping it ignorant used to supply for free."""
    prompt = WORKERS["write"].prompt
    check("the rule is stated", "never edit a test" in prompt.lower())


def test_the_writer_sees_the_evidence_against_its_own_work():
    """It applies the changes, so it is the node that has to react to them
    failing. It used to read only the task and a file list."""
    reads = WORKERS["write"].reads
    for kind in ("exec", "notes", "diff", "edits"):
        check(f"write can see {kind}", kind in reads)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("session: all checks passed")
