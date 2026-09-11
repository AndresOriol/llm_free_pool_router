"""The explorer's configuration, checked without the network.

Everything here is about what the agent is *told*, what it is *allowed*, and
which pool members it spends. Whether it then does good research is a question
for a real run, not for this file.

    python -m pytest tests/agent/test_explore.py
"""

import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langchain_core.messages import AIMessage

from agent.explore import prompt, research_tools, session
from agent.runtime.backend import RestrictedShellBackend


# -- What the model is told -------------------------------------------------

def test_prompt_leaves_no_placeholder_unreplaced():
    text = prompt.build(128_000, members=9)
    assert not re.findall(r"\{[a-z_]+\}", text)


def test_prompt_names_the_web_tools_and_the_deliverable():
    text = prompt.build(128_000, members=9,
                        extra_sections=[session.orchestrator_prompt()])
    # The deleted pair must not survive in prose: a prompt describing a tool
    # the agent does not have is a measured cause of failed calls.
    assert "web_search" not in text and "read_url" not in text
    # The whole point of this agent: the files are the output, not the reply.
    assert "/research/" in text
    assert "128,000 input tokens" in text


# -- Whether it can search at all ------------------------------------------

def test_a_run_with_no_tavily_account_is_refused_before_it_starts():
    class _EmptyPool:
        accounts = []

    try:
        session.check_pool(_EmptyPool())
    except session.NoSearchPool as exc:
        assert "TAVILY_API_KEY_1" in str(exc)
    else:
        raise AssertionError("a run with no way to search must refuse up front")


def test_one_tavily_account_works_and_is_warned_about():
    """It searches; it has nothing to fail over to. That is worth saying once,
    because the failure mode is every search dying at the monthly wall."""
    class _OnePool:
        accounts = [object()]

    assert session.check_pool(_OnePool()) == 1



# -- What it is allowed to do ------------------------------------------------

def test_the_explorer_runs_no_programs_at_all():
    root = Path(tempfile.mkdtemp())
    backend = RestrictedShellBackend(root_dir=str(root), allowed_programs=())
    result = backend.execute("python -c 'print(1)'")
    assert result.exit_code == 1
    # Refused readably, as a tool result rather than an exception -- and without
    # the "only runs []" that reads to a model like a bug to work around.
    assert "no shell here" in result.output
    assert "[]" not in result.output


def test_the_explorer_still_reads_and_writes_files():
    # The handoff to the coding agent is the filesystem, so this is the one
    # capability the explorer cannot lose.
    root = Path(tempfile.mkdtemp())
    backend = RestrictedShellBackend(root_dir=str(root), allowed_programs=())
    backend.write("/research/notes.md", "# what I found\n")
    assert (root / "research" / "notes.md").read_text() == "# what I found\n"
    assert "what I found" in (backend.read("/research/notes.md")
                              .file_data or {}).get("content", "")




# --- what the command reports ------------------------------------------------
# The explorer is reached by running it (docs/16-delegation.md), so its stdout
# is what a caller -- a person, or the coding agent that ran the command --
# reads back. The one piece of logic that is the explorer's own lives here:
# deciding which notes *this* run wrote.

from agent.explore import __main__ as explore_cli  # noqa: E402


def _report(capsys, workdir, notes, before=None, reply="done"):
    """`_summary` over a research directory holding `notes`. Returns stdout."""
    directory = workdir / session.RESEARCH_DIR
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in notes.items():
        (directory / name).write_text(text, encoding="utf-8")
    explore_cli._summary({"messages": [AIMessage(reply)]}, None, workdir, before)
    return capsys.readouterr().out


def test_the_final_message_is_the_output_of_the_command(capsys, tmp_path):
    """A caller that ran this reads stdout and nothing else, so the answer has
    to be in it -- first, and whole."""
    out = _report(capsys, tmp_path, {"limits.md": "# limits\n"},
                  reply="Cerebras allows 14,400 requests a day.")

    assert "Cerebras allows 14,400 requests a day." in out
    assert out.index("Cerebras") < out.index("=== DONE")


def test_a_long_final_message_is_not_clipped(capsys, tmp_path):
    """It was clipped to 2,000 characters when nobody read it. Now it is the
    answer a caller gets back, and half an answer is worse than none."""
    out = _report(capsys, tmp_path, {"n.md": "x"}, reply="y" * 5_000)
    assert "y" * 5_000 in out


def test_the_notes_this_run_wrote_are_named(capsys, tmp_path):
    out = _report(capsys, tmp_path, {"limits.md": "# limits\n"})

    assert "notes written by this run: 1" in out
    assert "research/limits.md" in out


def test_a_second_run_does_not_claim_the_first_ones_notes(capsys, tmp_path):
    """The failure this guards: a stale note read as the answer to a new
    question. Without the before-shot the second run reports both files."""
    _report(capsys, tmp_path, {"first.md": "# one\n"})
    before = explore_cli._notes(tmp_path)

    out = _report(capsys, tmp_path, {"second.md": "# two\n"}, before)

    assert "notes written by this run: 1" in out
    assert "research/second.md" in out
    assert "not this run's findings" in out and "research/first.md" in out


def test_a_rewritten_note_counts_as_this_runs_work(capsys, tmp_path):
    """Same path, new content -- the caller must be pointed at it again."""
    _report(capsys, tmp_path, {"note.md": "# one\n"})
    before = explore_cli._notes(tmp_path)

    out = _report(capsys, tmp_path, {"note.md": "# rewritten, longer\n"}, before)

    assert "notes written by this run: 1" in out
    assert "research/note.md" in out


def test_a_run_that_wrote_nothing_says_so_loudly(capsys, tmp_path):
    """Silence here reads as success and is not: the run spent quota and left
    nothing behind (docs/15-explorer.md#151-what-it-is-for)."""
    out = _report(capsys, tmp_path, {}, reply="I researched it thoroughly.")

    assert "notes written by this run: none" in out
