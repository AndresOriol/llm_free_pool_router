"""The agent protocol, checked without the network and without the pool.

Two things can be wrong here silently, and both are what this file watches.

The first is **drift from the spec**. The whole argument for adopting A2A rather
than inventing a message format is that the objects on the local transport are
the ones an HTTP binding would serialize
([16.2](../../docs/16-agent-protocol.md#162-why-a2a-and-why-not-the-alternatives)),
and a field quietly renamed to something more Pythonic costs exactly that and
nothing else would notice. So the field names are asserted literally.

The second is **a delegation that looks like it worked**. A task that comes back
`completed` with the previous task's notes attached reads as success and is a
stale answer to a new question; so does one that reports its delegate's crash as
silence. Those paths are asserted by outcome, not by exception type.
"""

import json
from pathlib import Path

from agent.protocol import (AgentCard, AgentRegistry, AgentSkill, Artifact,
                            FilePart, LocalTransport, Message, Task, TaskState,
                            TaskStore, directory_section, render)

CARD = AgentCard(name="explore", version="0.1.0",
                 description="Researches the open web.",
                 skills=[AgentSkill(id="web_research", name="Web research",
                                    description="Answer and cite.",
                                    examples=["What are Cerebras' limits?"])])


def _transport(handler, store=None):
    registry = AgentRegistry()
    registry.register(CARD, handler)
    return LocalTransport(registry, store)


def _ok(note="research/a.md"):
    def handler(task):
        task.artifacts.append(Artifact(name=Path(note).name,
                                       parts=[FilePart(uri=note)],
                                       metadata={"bytes": 12}))
        return task.advance(TaskState.COMPLETED, Message.agent("Wrote one note."))
    return handler


# --- the wire form ----------------------------------------------------------


def test_task_serializes_to_the_specs_field_names():
    task = _transport(_ok()).message_send("explore", Message.user("q"))
    wire = task.to_dict()

    assert wire["kind"] == "task"
    assert set(wire) == {"kind", "id", "contextId", "status", "artifacts",
                         "history", "metadata"}
    assert wire["status"]["state"] == "completed"
    assert wire["history"][0]["role"] == "user"      # the caller, not a human
    assert wire["artifacts"][0]["parts"][0]["kind"] == "file"
    assert wire["artifacts"][0]["parts"][0]["file"]["mimeType"] == "text/markdown"


def test_a_task_survives_a_json_round_trip_unchanged():
    """What makes an HTTP binding a transport swap rather than a migration."""
    task = _transport(_ok()).message_send("explore", Message.user("q"))
    again = Task.from_dict(json.loads(json.dumps(task.to_dict())))
    assert again.to_dict() == task.to_dict()


def test_the_card_is_addressed_honestly():
    card = CARD.to_dict()
    assert card["url"] == "local:explore"        # not a URL anyone can call
    assert card["preferredTransport"] == "LOCAL"
    assert card["capabilities"]["streaming"] is False
    assert card["skills"][0]["id"] == "web_research"


def test_an_artifact_carries_a_path_and_never_the_bytes():
    """A note is thousands of tokens; the caller decides whether to pay them."""
    part = FilePart(uri="research/a.md").to_dict()
    assert part["file"]["uri"] == "research/a.md"
    assert "bytes" not in part["file"]


# --- the lifecycle ----------------------------------------------------------


def test_the_handler_sees_a_working_task_and_a_terminal_one_comes_back():
    seen = []

    def handler(task):
        seen.append(task.state)
        return task.advance(TaskState.COMPLETED, Message.agent("done"))

    task = _transport(handler).message_send("explore", Message.user("q"))
    assert seen == [TaskState.WORKING]
    assert task.done and task.state == TaskState.COMPLETED


def test_a_delegate_that_crashes_fails_the_task_rather_than_the_caller():
    def boom(task):
        raise RuntimeError("all providers exhausted")

    task = _transport(boom).message_send("explore", Message.user("q"))
    assert task.state == TaskState.FAILED
    assert "all providers exhausted" in task.status.message.text


def test_an_unknown_agent_is_rejected_with_the_names_that_would_work():
    task = _transport(_ok()).message_send("nobody", Message.user("q"))
    assert task.state == TaskState.REJECTED
    assert "explore" in task.status.message.text


def test_a_handler_that_forgets_to_finish_still_ends_terminal():
    """A task left `working` would be waited on by a caller that never returns."""
    task = _transport(lambda t: t).message_send("explore", Message.user("q"))
    assert task.done


def test_an_empty_request_is_refused_before_it_costs_anything():
    from agent.protocol.tools import make_delegate_tool

    calls = []
    transport = _transport(lambda t: calls.append(1) or t)
    result = make_delegate_tool(transport).invoke({"agent": "explore",
                                                   "request": "   "})
    assert "error" in result and not calls


# --- what the caller reads back --------------------------------------------


def test_the_tool_result_names_the_files_and_not_their_contents():
    task = _transport(_ok("research/limits.md")).message_send(
        "explore", Message.user("q"))
    text = render(task, "explore")
    assert "completed" in text and "`research/limits.md`" in text
    assert "Wrote one note." in text


def test_completed_with_nothing_written_says_so():
    """Silence here reads as success and is not."""
    def empty(task):
        return task.advance(TaskState.COMPLETED, Message.agent("I looked."))

    text = render(_transport(empty).message_send("explore", Message.user("q")),
                  "explore")
    assert "no files" in text


# --- discovery --------------------------------------------------------------


def test_the_directory_is_prose_and_disappears_when_there_are_no_peers():
    registry = AgentRegistry()
    assert directory_section(registry) == ""
    registry.register(CARD, _ok())
    section = directory_section(registry)
    assert "explore" in section and "web_research" in section


def test_the_coding_agent_gains_exactly_one_tool():
    """The delegating configuration must differ from the baseline by one tool."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from agent.code.session import build_agent

    def names(agent):
        node = agent.nodes.get("tools")
        return set(node.bound._tools_by_name) if node else set()

    model = FakeListChatModel(responses=["ok"])
    workdir = Path(__file__).parent
    plain = names(build_agent(workdir, model))
    with_peer = names(build_agent(workdir, model, transport=_transport(_ok())))

    assert with_peer - plain == {"delegate"}


# --- the record -------------------------------------------------------------


def test_a_terminal_task_is_written_beside_the_run_record(tmp_path):
    store = TaskStore(tmp_path / "a2a")
    task = _transport(_ok(), store).message_send("explore", Message.user("q"))

    written = json.loads((tmp_path / "a2a" / f"{task.id}.json")
                         .read_text(encoding="utf-8"))
    assert written["status"]["state"] == "completed"
    assert written["history"][0]["parts"][0]["text"] == "q"


def test_the_task_can_be_read_back_by_id():
    transport = _transport(_ok())
    task = transport.message_send("explore", Message.user("q"))
    assert transport.tasks_get(task.id) is task
    assert transport.tasks_get("no-such-id") is None
