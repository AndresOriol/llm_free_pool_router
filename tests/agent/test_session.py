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

from agent.harness.blackboard import Blackboard
from agent.harness.envelope import Brief, parse_brief, parse_report
from agent.harness.loop import RoleResult, Stats, run_role
from agent.harness.roles import SESSION_ORCHESTRATE, SESSION_ROLES
from agent.harness.session import (Git, Journal, Step, Transcript,
                                   append_to_notes, build_report, read_notes,
                                   replay, run_session, write_rationale)
from agent.harness.tools import make_tools
from agent.restricted_backend import RestrictedShellBackend
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
    check("write can read", "read_lines" in SESSION_ROLES["write"].tools)
    check("but still cannot search", "search_code" not in SESSION_ROLES["write"].tools)


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
    result = RoleResult(
        text="Everything passes, we are done!",
        outputs=[("run_command", "exit=1\nE   assert 1 == 3")],
        calls=[{"name": "run_command", "args": {"command": "python -m pytest"},
                "output": "exit=1\nE   assert 1 == 3"}])
    report = build_report("execute", result)
    check("failure is not DONE", report.status != "DONE")
    check("the command is on the record",
          report.evidence == [["python -m pytest", 1]])

    ok = RoleResult(text="", outputs=[("run_command", "exit=0\n2 passed")],
                    calls=[{"name": "run_command", "args": {"command": "python -m pytest"},
                            "output": "exit=0\n2 passed"}])
    check("success is DONE", build_report("execute", ok).status == "DONE")


def test_writer_is_judged_by_what_it_applied():
    described = RoleResult(text="I would change line 4 to lowercase the key.",
                           outputs=[], calls=[])
    check("describing an edit is not making one",
          build_report("write", described).status == "PARTIAL")

    applied = RoleResult(text="", outputs=[("replace_in_file", "ok: replaced 1 in /r.py")],
                         calls=[])
    check("an applied edit is DONE", build_report("write", applied).status == "DONE")


def test_a_role_can_refuse_to_guess():
    """The return path that turns a wasted cycle into an informative one."""
    result = RoleResult(text="STATUS: INSUFFICIENT_CONTEXT\nFINDING: no file named",
                        outputs=[], calls=[])
    report = build_report("write", result)
    check("status survives from an acting role", report.needs_context)
    check("what is missing is carried up", "no file named" in report.finding)

    # ...but it must never overwrite a demonstrated success.
    did_both = RoleResult(text="INSUFFICIENT_CONTEXT",
                          outputs=[("replace_in_file", "ok: replaced 1 in /r.py")], calls=[])
    check("evidence of success wins", build_report("write", did_both).status == "DONE")


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

    bb = Blackboard(task="t")
    replayed = replay(journal, bb)
    check("only completed steps are replayed", replayed == 2)
    check("findings are restored", any("/retry.py" in n for n in bb.notes))
    check("evidence is restored", any("exit 1" in line for line in bb.log))
    check("the step count carries over", bb.cycles == 2)


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
          SESSION_ORCHESTRATE.min_context > 12_000)
    for name in ("document", "review"):
        check(f"{name} needs breadth", SESSION_ROLES[name].min_context > 12_000)
    for name in ("explore", "write", "execute"):
        check(f"{name} runs anywhere", SESSION_ROLES[name].min_context == 0)


def test_narrow_roles_still_fit_the_smallest_member():
    """The budget claim, per tier: the roles that run anywhere must still fit
    the 6,000-TPM member even on a saturated blackboard."""
    root = seed_project()
    backend = RestrictedShellBackend(root_dir=str(root))
    toolset = make_tools(backend)
    bb = Blackboard(task="fix the retry helper")
    for i in range(30):
        bb.add_note(f"finding number {i} " + "y" * 400)
        bb.add_files([f"/mod{i}.py"])
        bb.add_edit(f"ok: edit {i}")
    bb.set_exec("E   assert 1 == 3\n" * 400, False)
    bb.set_diff("+++ b/retry.py\n" + "+    x = 1\n" * 400)
    brief = Brief(action="WRITE", goal="g" * 300, context="c" * 1_200,
                  done_when="d" * 300)

    worst = 0
    for name in ("explore", "write", "execute"):
        stats = Stats()
        run_role(ScriptedModel([ai("ok")]), SESSION_ROLES[name], toolset, bb, {},
                 stats, brief=brief)
        worst = max(worst, stats.prompt_tokens)
    check(f"worst narrow-role prompt {worst} tok must fit a 6k-TPM model",
          worst < 5_400)
    return worst


# --- the loop --------------------------------------------------------------

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

    bb, stats, outcome, steps = run_session(model, backend, toolset,
                                            "add() is wrong", root, max_steps=8)

    check(f"outcome was {outcome}", outcome == "done")
    check("the file is actually fixed", (root / "calc.py").read_text() == FIXED)
    check("the run verified itself", bb.exec_ok is True)
    check("an unreviewed done was refused",
          any("nothing has been reviewed" in line for line in bb.log))
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

    bb, stats, outcome, steps = run_session(model, backend, toolset,
                                            "add() is wrong", root, max_steps=6)
    actions = [s["action"] for s in steps]
    check(f"a write was forced, got {actions}", "write" in actions)
    check("and it is recorded as a refusal",
          any("EXPLORE refused" in line for line in bb.log))


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

    bb, stats, outcome, steps = run_session(model, backend, toolset,
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

    bb, stats, outcome, steps = run_session(model, backend, toolset,
                                            "add() is wrong", root, max_steps=2)

    check("the transcript was still written",
          list((root / ".harness" / "steps").glob("*.md")))
    check(f"and it is nowhere in the diff:\n{bb.diff[:300]}",
          ".harness" not in bb.diff)
    check("while the real change is", "calc.py" in bb.diff)


def test_a_resumed_session_does_not_overwrite_its_transcript():
    """R2 says a killed session resumes. Turn 1 of the second life must not
    land on top of turn 1 of the first, or the crash erases its own cause."""
    root = seed_project()
    directory = root / ".harness" / "steps"
    first = Transcript(directory)
    first.write("explore", RoleResult(text="a", outputs=[], prompt="p"))
    resumed = Transcript(directory)
    resumed.write("write", RoleResult(text="b", outputs=[], prompt="q"))
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
    bb, stats, outcome, steps = run_session(model, backend, toolset, "fix it",
                                            root, max_steps=6)
    check(f"outcome was {outcome}", outcome == "giveup")
    rationale = next((root / ".harness" / "reports").glob("session-*.md")).read_text()
    check("the open question is written down", "no file was named" in rationale)
    check("the notes were still updated", (root / "NOTES.md").is_file())


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("session: all checks passed")
