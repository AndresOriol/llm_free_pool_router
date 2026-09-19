"""Delegation by command line, checked without a pool and without a subprocess.

Three things can be wrong here silently, and each of them cost something once.

**A delegation that cannot start.** The child is a real process, so it needs
three things: the pool's keys, this repository on `PYTHONPATH`, and more than
the seconds a `pytest` run is sized for. The first two come from the backend's
environment and the third is asked for per call, so any one of them missing
turns every delegation into a failure minutes in, after the brief has already
been written.

**A configuration that is not comparable.** `AGENT_PEERS=` is the baseline arm
of the A/B ([How to propose a change](../../docs/status.md#how-to-propose-a-change)). If
the delegating configuration differed by a *tool* rather than a paragraph, the
comparison would be measuring a schema on every step and not the delegation.
That is exactly what the old `delegate` tool did, and it is why this shape is
worth asserting.

**A delegation that looks like it worked.** A session that committed nothing
reports a cheerful sign-off like any other, so the verdict from `git` leads and
the prose follows it.
"""

import os
import subprocess
from pathlib import Path

from agent import delegation
from agent.code import agent as code
from agent.utils import gitstate


# --- what is offered --------------------------------------------------------


def test_no_peers_means_no_section_and_no_tool(monkeypatch):
    """The baseline arm: the agent is exactly what it was before."""
    monkeypatch.setenv(delegation.PEERS_ENV, "")
    assert delegation.requested() == ()
    assert delegation.available() == []
    assert delegation.prompt_section([]) == ""


def test_the_default_is_on_so_it_gets_exercised(monkeypatch):
    monkeypatch.delenv(delegation.PEERS_ENV, raising=False)
    assert delegation.requested() == ("explore",)


def test_an_unknown_peer_is_dropped_rather_than_offered(monkeypatch):
    monkeypatch.setenv(delegation.PEERS_ENV, "code,nonesuch")
    assert delegation.available() == ["code"]


def test_scenarios_is_not_offered_without_the_repository(monkeypatch, tmp_path):
    """An agent advertised and then unable to work costs a whole session."""
    monkeypatch.setenv(delegation.SCENARIOS_ENV, str(tmp_path / "absent"))
    assert delegation.available(("scenarios",)) == []

    repo = tmp_path / "present"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setenv(delegation.SCENARIOS_ENV, str(repo))
    assert delegation.available(("scenarios",)) == ["scenarios"]


def test_web_reachable_imports_exist():
    """_web_reachable depends on NoSearchPool and search_pool; assert they exist
    where imported so a rename doesn't silently disable the explore peer."""
    from llm_router.tavily_router import NoSearchPool

    from agent.explore.tools import search_pool

    assert issubclass(NoSearchPool, SystemExit)
    assert callable(search_pool)


def test_the_prompt_section_holds_the_pointer_and_nothing_else():
    """What is on every call is a sentence sending the agent to the skill. The
    roster and the how-to are read only when it decides it needs them."""
    section = delegation.prompt_section(["explore"])
    assert "`delegate` skill" in section
    assert "python -m agent.explore" not in section
    assert len(section) < 250, "this is charged on every step of every run"


def test_the_skill_names_the_command_the_agent_must_run():
    values = delegation.skill_values(["explore"], Path("."))
    assert "python -m agent.explore . --task" in values["roster"]
    assert "/research" in values["roster"], "it says where the answer will be"


def test_the_skill_description_says_when_to_open_it():
    """The description is the gate: it is all the model sees until it decides
    to read the body, so a description that does not state the trigger is a
    skill that is never read."""
    description = delegation.skill_values(["explore"], Path("."))["description"]
    assert "explore" in description, "it names who this run can actually reach"
    assert "python -m agent.*" in description, "the command that should stop it"
    assert "do it yourself" in description, "and when not to open it"
    assert len(description) <= 1024, "the Agent Skills limit on a description"


def test_the_coding_agent_gains_no_tool_at_all():
    """**This is what makes the A/B valid.** The delegating configuration
    differs from the baseline by a paragraph of prompt and nothing else; if it
    differed by a tool, the comparison would be measuring a schema charged on
    every step ([Why it is shaped this way](../../docs/agents/code.md#why-it-is-shaped-this-way))."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from agent.code.agent import build_agent

    def names(agent):
        node = agent.nodes.get("tools")
        return set(node.bound._tools_by_name) if node else set()

    model = FakeListChatModel(responses=["ok"])
    workdir = Path(__file__).parent
    plain = names(build_agent(workdir, model))
    with_peer = names(build_agent(workdir, model, peers=["explore"]))

    assert with_peer == plain


# --- what the child is given ------------------------------------------------


def test_a_child_cannot_delegate_back_to_the_agent_that_ran_it(monkeypatch):
    """What keeps the graph acyclic, with no cycle check anywhere."""
    monkeypatch.setenv(delegation.PEERS_ENV, "explore,code")
    assert delegation.child_env("code")[delegation.PEERS_ENV] == "explore"


def test_peers_are_filtered_not_replaced(monkeypatch):
    """An operator who turned delegation off must not get it back through a
    session someone else delegated."""
    monkeypatch.setenv(delegation.PEERS_ENV, "")
    assert delegation.child_env("code")[delegation.PEERS_ENV] == ""


def test_the_child_keeps_the_flat_trace_and_loses_the_run_tree(monkeypatch):
    """The JSONL is appended to and is what every metric is summed over, so a
    delegation's cost stays inside the totals of the run that asked for it. The
    run tree is one file per run, and a child writing the parent's path would
    overwrite the record of the run that launched it."""
    monkeypatch.setenv("EVAL_TRACE_FILE", "/runs/r1/trace.jsonl")
    monkeypatch.setenv("AGENT_TRACE_FILE", "/runs/r1/trace.json")

    env = delegation.child_env("explore")

    assert env["EVAL_TRACE_FILE"] == "/runs/r1/trace.jsonl"
    assert "AGENT_TRACE_FILE" not in env


def test_the_child_can_import_this_repository_from_anywhere(monkeypatch):
    """`python -m agent.explore` runs with the *workspace* as its cwd, which is
    routinely some other project entirely."""
    monkeypatch.delenv("PYTHONPATH", raising=False)
    assert delegation.child_env("explore")["PYTHONPATH"] == str(
        delegation.HARNESS_ROOT)

    monkeypatch.setenv("PYTHONPATH", "/already/here")
    both = delegation.child_env("explore")["PYTHONPATH"]
    assert both.startswith(str(delegation.HARNESS_ROOT))
    assert both.endswith("/already/here")


# --- what the backend hands the child ---------------------------------------


def test_the_backend_puts_this_repository_on_the_child_pythonpath(tmp_path,
                                                                 monkeypatch):
    """`python -m agent.explore` runs with the *workspace* as its cwd, which is
    routinely some other project entirely. The env the coding agent's backend
    gives a command is the only thing that makes the import work."""
    monkeypatch.delenv("PYTHONPATH", raising=False)
    assert code.local_shell(tmp_path)._env["PYTHONPATH"] == str(
        delegation.HARNESS_ROOT)

    monkeypatch.setenv("PYTHONPATH", "/already/here")
    both = code.local_shell(tmp_path)._env["PYTHONPATH"]
    assert both.startswith(str(delegation.HARNESS_ROOT))
    assert both.endswith("/already/here")


def test_the_child_inherits_the_pool_keys(tmp_path, monkeypatch):
    """A delegated session *is* the pool's consumer: it builds a router, and
    without the keys it dies at start-up with 'no providers loaded'."""
    monkeypatch.setenv("PROBE_FAKE_API_KEY", "secret")
    assert code.local_shell(tmp_path)._env["PROBE_FAKE_API_KEY"] == "secret"


def test_the_delegate_skill_asks_for_the_hour_a_session_needs(tmp_path):
    """The backend's ceiling is sized for a test run, and nothing in Python
    knows that one command is a delegation. The model is what asks for the
    longer timeout, so the skill has to tell it to
    ([Delegation](../../docs/agents/delegation.md))."""
    text = (Path(code.__file__).parent / "skills" / "delegate"
            / "SKILL.md").read_text(encoding="utf-8")
    assert "timeout=3600" in text


# --- what comes back --------------------------------------------------------


def test_run_names_the_module_the_workdir_and_the_task(monkeypatch, tmp_path):
    seen = {}

    class Done:
        returncode, stdout, stderr = 0, "said something", ""

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return Done()

    monkeypatch.setattr(subprocess, "run", fake_run)
    code, output = delegation.run("explore", tmp_path, "find out X")

    assert seen["argv"][1:] == ["-m", "agent.explore", str(tmp_path),
                                "--task", "find out X"]
    assert (code, output) == (0, "said something")


def test_a_timeout_is_reported_as_a_stop_and_not_a_crash(monkeypatch, tmp_path):
    """The session did real work and was stopped; whatever it committed is
    still committed, and an empty failure would say the opposite."""
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1, output="got this far")

    monkeypatch.setattr(subprocess, "run", fake_run)
    code, output = delegation.run("code", tmp_path, "fix it", timeout=1)

    assert code is None
    assert "got this far" in output


def test_a_child_that_cannot_be_launched_is_a_message_not_an_exception(
        monkeypatch, tmp_path):
    def fake_run(argv, **kwargs):
        raise OSError("no python here")

    monkeypatch.setattr(subprocess, "run", fake_run)
    code, output = delegation.run("code", tmp_path, "fix it")

    assert code == 1 and "no python here" in output


# --- the verdict ------------------------------------------------------------


def test_a_session_that_committed_nothing_says_so_before_its_own_prose():
    """The first delegation this project ever made came back describing three
    changes to `session.py` that the diff did not contain."""
    said = gitstate.render({"git": True, "branch": "master", "commits": 0,
                            "files_changed": []})

    assert "Nothing changed." in said
    assert "the repository disagrees" in said
    assert not gitstate.moved({"git": True, "commits": 0, "files_changed": []})


def test_a_session_that_moved_the_repository_names_what_moved():
    said = gitstate.render({"git": True, "branch": "improve/t", "commits": 2,
                            "files_changed": ["agent/code/prompt.py"]})

    assert "improve/t" in said and "2 commit(s)" in said
    assert "agent/code/prompt.py" in said


def test_uncommitted_work_is_named_because_it_looks_like_nothing():
    """A run that edited files and never committed them is indistinguishable
    from one that did nothing, in every other field."""
    said = gitstate.render({"git": True, "branch": "b", "commits": 0,
                            "files_changed": [], "uncommitted": ["a.py"]})

    assert "1 left uncommitted" in said
    assert gitstate.moved({"git": True, "commits": 0, "uncommitted": ["a.py"]})


def test_a_workspace_with_no_history_is_not_a_failure():
    """Not every workspace is a git repository, and one that is not must not
    read as a session that did nothing."""
    assert gitstate.moved({"git": False}) is True
    assert "not a git repository" in gitstate.render({"git": False})
