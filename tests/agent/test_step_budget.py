"""Spending the step budget must end a session, not kill it.

A recorded run reached the old 120-superstep limit in 220 seconds of
productive work -- it had installed a toolchain, written five files and
compiled them -- and `GraphRecursionError` came straight out of
`agent.invoke`. That skipped the summary and the run record, so a session
whose code compiled reported nothing and left it uncommitted. These tests
pin the two halves of the fix: the state survives the stop, and the session
is told about it in time to land its work.
"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from agent.code.agent import (RECURSION_LIMIT, WRAP_UP_RESERVE, _drain,
                              _settle, prompt, system_prompt, template_values)


class _Agent:
    """A graph that yields `n` states and then runs out of supersteps."""

    def __init__(self, states, raise_at_end=True):
        self.states = states
        self.raise_at_end = raise_at_end
        self.limits = []

    def stream(self, state, config, stream_mode=None):
        self.limits.append(config["recursion_limit"])
        yield from self.states
        if self.raise_at_end:
            raise GraphRecursionError("Recursion limit reached")


def _ai(*call_ids):
    return AIMessage(content="", tool_calls=[
        {"name": "execute", "args": {}, "id": c} for c in call_ids])


def test_drain_keeps_the_last_state_when_the_budget_runs_out():
    agent = _Agent([{"messages": [1]}, {"messages": [1, 2]}])
    state, ran_out = _drain(agent, {"messages": []}, {}, 100)
    assert ran_out is True
    # The point of streaming: the work done before the stop is still here.
    assert state == {"messages": [1, 2]}


def test_drain_reports_a_clean_finish_as_not_ran_out():
    agent = _Agent([{"messages": [1]}], raise_at_end=False)
    state, ran_out = _drain(agent, {"messages": []}, {}, 100)
    assert ran_out is False
    assert state == {"messages": [1]}


def test_drain_passes_the_limit_it_was_given():
    agent = _Agent([], raise_at_end=False)
    _drain(agent, {}, {"configurable": {"x": 1}}, 37)
    assert agent.limits == [37]


def test_drain_returns_the_input_when_nothing_streamed():
    agent = _Agent([])
    state, ran_out = _drain(agent, {"messages": ["seed"]}, {}, 5)
    assert ran_out is True
    assert state == {"messages": ["seed"]}


def test_settle_drops_a_tool_call_nothing_answered():
    messages = [HumanMessage("go"), _ai("call_1")]
    assert _settle(list(messages)) == [messages[0]]


def test_settle_keeps_a_tool_call_that_was_answered():
    messages = [HumanMessage("go"), _ai("call_1"),
                ToolMessage(content="ok", tool_call_id="call_1")]
    assert _settle(list(messages)) == messages


def test_settle_drops_a_partially_answered_call_too():
    """One of two calls answered is still a shape the providers reject."""
    messages = [HumanMessage("go"), _ai("call_1", "call_2"),
                ToolMessage(content="ok", tool_call_id="call_1")]
    settled = _settle(list(messages))
    assert settled == [messages[0]]


def test_settle_leaves_a_plain_answer_alone():
    messages = [HumanMessage("go"), AIMessage(content="done")]
    assert _settle(list(messages)) == messages


def test_settle_unwinds_more_than_one_dangling_turn():
    messages = [HumanMessage("go"), _ai("call_1"), _ai("call_2")]
    assert _settle(list(messages)) == [messages[0]]


def test_settle_survives_a_history_that_is_all_dangling():
    assert _settle([_ai("call_1")]) == []


def test_the_reserve_is_a_slice_of_the_budget_not_the_whole_thing():
    assert 0 < WRAP_UP_RESERVE < RECURSION_LIMIT // 2


def test_the_budget_is_sized_past_the_run_that_exposed_it():
    """The run that motivated this reached 120 supersteps doing real work."""
    assert RECURSION_LIMIT > 120


def test_the_wrap_up_asks_for_a_commit_and_a_handover():
    text = prompt("wrap_up.md", {"used": 360, "limit": 400, "left": 40})
    assert "commit" in text.lower()
    # It has to say the stop is coming, or there is no reason to change course.
    assert "40" in text and "400" in text
    assert "not start anything new" in text.lower()


def test_the_wrap_up_asks_for_the_account_before_the_commit():
    """A probe spent its whole reserve on the commit and said nothing.

    The reply is the only part that survives being cut off mid-command, so it
    has to be asked for first.
    """
    text = prompt("wrap_up.md", {"used": 360, "limit": 400, "left": 40}).lower()
    assert text.index("say what is done") < text.index("then commit")


def test_the_summary_reports_a_stopped_run_differently_from_a_finished_one(capsys):
    from agent.code.__main__ import _summary
    state = {"messages": [AIMessage(content="what I got done")]}
    _summary(state, None)
    assert "DONE" in capsys.readouterr().out
    _summary({**state, "step_budget_spent": True}, None)
    out = capsys.readouterr().out
    assert "STOPPED (step budget spent)" in out
    # The account still has to reach the reader on a stopped run.
    assert "what I got done" in out


def test_the_summary_finds_the_last_message_that_has_words():
    """A stopped run ends on an empty tool call; the account is further back."""
    from agent.utils.cli import text
    assert text(AIMessage(content="", tool_calls=[])) == ""
    assert text(AIMessage(content="  the account  ")) == "the account"
    assert text(AIMessage(content=[{"type": "text", "text": "blocks"}])) == "blocks"


def test_the_prompt_states_no_rule_the_shell_does_not_enforce():
    """`execute` is the host shell, so every sentence the prompt used to spend
    on the allowlist -- which programs run, no `&&`, wrap the rest in
    `python -c` -- is now false. A rule stated and not enforced is worse than
    no rule: the model plans around it and pays for the detour."""
    text = system_prompt(template_values(128_000, 70))

    assert "## Running commands" not in text
    assert "&&" not in text
    assert "python -c" not in text
