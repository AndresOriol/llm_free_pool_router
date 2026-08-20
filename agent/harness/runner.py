"""Running one role: one bounded call, then throw the conversation away.

This is the mechanism the whole harness rests on. A role is handed a system
prompt, its declared slice of the blackboard and one ask; it gets a few rounds
of tool calls; and then **the message list is discarded**. Only the RoleResult
escapes, and only what the caller folds into the blackboard survives into the
next step.

That is why a prompt here is bounded by what a role declares rather than by how
long the run has been going -- and it is why this is written by hand instead of
using a prebuilt LangGraph agent. `create_react_agent` accumulates the
conversation, which is exactly the cost this exists to avoid; and `max_rounds`
and `force_summary` below both come from observed failures and have no prebuilt
equivalent. The *graph* is LangGraph's job (agent/harness/graph.py); the inside
of one node is this.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from llm_router.base_provider import estimate_tokens
from agent.harness.blackboard import Blackboard

logger = logging.getLogger("harness")


@dataclass
class Stats:
    """Per-run accounting, so the token claim can be checked rather than asserted."""
    calls: int = 0
    prompt_tokens: int = 0
    by_role: dict = field(default_factory=dict)

    def add(self, role: str, tokens: int) -> None:
        self.calls += 1
        self.prompt_tokens += tokens
        slot = self.by_role.setdefault(role, {"calls": 0, "tokens": 0})
        slot["calls"] += 1
        slot["tokens"] += tokens


@dataclass
class RoleResult:
    text: str
    outputs: list
    # The same tool calls with their arguments kept. `outputs` is enough to
    # decide what happened; `calls` is what lets a session say *which command*
    # produced an exit code, which is the substrate a rationale has to cite.
    calls: list = field(default_factory=list)
    # The exact prompt this role was handed, before any tool call. Without it a
    # post-mortem can see that a role went wrong but not whether it was given
    # what it needed -- and in a design whose whole mechanism is the
    # orchestrator curating context downward, that is the question worth
    # asking. The system prompt is role-static and lives in roles.py; only this
    # half varies per step.
    prompt: str = ""


def run_role(model, role, toolset, bb: Blackboard, config: dict,
             stats: Stats, force_summary: bool = False, brief=None) -> RoleResult:
    """Run one role to completion and throw away its conversation.

    Only the return value reaches the blackboard. The messages built here are
    local to the call, which is what keeps step 40 as cheap as step 1.

    `force_summary` spends the final round with no tools bound. A role that
    used every round on tool calls otherwise ends holding a tool-calling
    response, whose text is empty -- so it does all the work and reports
    nothing, and the next role gets an empty blackboard section.

    `brief` replaces the role's static instruction with one written for this
    step by the orchestrator (agent/harness/envelope.py). The role still sees
    only its declared blackboard sections, so the brief adds context rather than
    replacing the budget that bounds it.
    """
    # A role that declares a context floor is asking to be routed to a
    # wide-context member. Applied before bind_tools so the copy carries both.
    if role.min_context and hasattr(model, "for_context"):
        model = model.for_context(role.min_context)

    tools = [toolset[name] for name in role.tools if name in toolset]
    bound = model.bind_tools(tools) if tools else model
    # Only worth forcing on a role whose value is what it says, and only when
    # there is a round to spare. On an acting role it steals the round it
    # needed to act (see Role.reports).
    forcing = force_summary and role.reports and tools and role.max_rounds >= 2

    ask = brief.render() if brief is not None else f"# Your job\n{role.instruction}"
    messages = [
        SystemMessage(content=role.prompt),
        HumanMessage(content=f"{bb.render(role.sections)}\n\n{ask}"),
    ]

    outputs, calls_made, text = [], [], ""
    for round_no in range(role.max_rounds):
        final = round_no == role.max_rounds - 1
        if forcing and final:
            messages.append(HumanMessage(
                content="No more tool calls. Answer now, in plain text."))
            stats.add(role.name, estimate_tokens(messages, None))
            text = _text_of(model.invoke(messages, config=config))
            break

        stats.add(role.name, estimate_tokens(messages, tools))
        response = bound.invoke(messages, config=config)
        text = _text_of(response)

        calls = getattr(response, "tool_calls", None) or []
        if not calls:
            break

        messages.append(response)
        for call in calls:
            out = _invoke_tool(toolset, call, config)
            outputs.append((call.get("name", "?"), out))
            calls_made.append({"name": call.get("name", "?"),
                               "args": call.get("args") or {}, "output": out})
            messages.append(ToolMessage(content=out, tool_call_id=call.get("id", "")))

    return RoleResult(text=text, outputs=outputs, calls=calls_made,
                      prompt=messages[1].content)


def _text_of(response) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, list):  # some providers return content blocks
        content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    return str(content or "").strip()


def _invoke_tool(toolset, call, config=None) -> str:
    name = call.get("name")
    tool = toolset.get(name)
    if tool is None:
        # Name the alternatives: a small model that invented a tool usually
        # picks a real one when shown the list, rather than repeating itself.
        return f"error: no tool named {name!r}. Available: {', '.join(sorted(toolset))}"
    try:
        # config carries the eval tracer, so tool calls land in the trace the
        # metrics are derived from.
        return str(tool.invoke(call.get("args") or {}, config=config))
    except Exception as exc:  # noqa: BLE001 - the model sees the error and retries
        return f"error: {type(exc).__name__}: {exc}"
