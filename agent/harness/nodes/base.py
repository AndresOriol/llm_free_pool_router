"""What every node is, and what running one does.

A node is a separate LLM call with its own system prompt, its own tool set and
its own slice of the session's log. Nothing is shared between nodes except that
log, so a node's prompt size is set by its `reads` and `tools`, not by how long
the run has been going -- which is what lets step 40 cost what step 1 cost, and
is the whole reason this exists instead of one growing conversation.

Four fields carry most of the design:

- **`reads`** is the context budget: the kinds of log entry this node is handed
  (agent/harness/log.py). A node sees exactly what it declares and nothing else;
  the orchestrator makes up the difference by writing facts into the brief
  (agent/harness/protocol.py). Context is pushed down, never pulled up.
- **`min_context`** is a claim about the *job*, not the request. A node whose
  work is judgement over a wide view declares a floor and the router honours it
  (llm_router/router.py). Splitting work to fit the narrowest pool member is what
  makes this cheap; doing it to a node that needs breadth is what makes it stupid.
- **`report`** turns the node's raw result into a verdict, and **`absorb`** says
  what of it the next node gets to see. Both live in the node's own file, so
  reading one file tells you everything about that node.

`run_node` below is the mechanism the whole harness rests on: one bounded call,
then **the message list is discarded**. Only the NodeResult escapes, and only
what `absorb` appends to the log survives into the next step. That is why
this is written by hand rather than with a prebuilt LangGraph agent --
`create_react_agent` accumulates the conversation, which is exactly the cost
this exists to avoid, and `max_rounds` and `force_summary` come from observed
failures and have no prebuilt equivalent. The *graph* is LangGraph's job
(agent/harness/graph.py); the inside of one node is this.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from llm_router.base_provider import estimate_tokens
from agent.harness.log import Log
from agent.harness.nodes.shared import report_from_text

# Shared preamble. Every node pays for this, so it stays at three lines.
COMMON = (
    "You work inside a Python project. The project root is `/`; all paths are "
    "relative to it and you cannot escape it. There is no shell.\n"
    "Answer with tool calls, not explanations. Be brief."
)

# Only the wide-context members of the pool clear this. Groq tops out at 12,000.
WIDE = 50_000


@dataclass(frozen=True)
class Node:
    """One narrow agent: what it is told, what it may touch, what it may see."""

    name: str
    prompt: str
    tools: tuple = ()       # tool names from agent.runtime.tools.make_tools
    reads: tuple = ()       # kinds of log entry this node is allowed to see
    max_rounds: int = 2     # tool-call rounds before the node is cut off
    instruction: str = ""   # the ask, when no brief is written for this step
    # Is this node's value its *text* (a report) or its *tool effects* (an
    # action)? Only a reporting node benefits from a forced no-tools final
    # round. Forcing one on an acting node steals the round it needed to act:
    # an edit node given two rounds, one of them tool-less, spent the first
    # thinking and then could only describe the fix it never applied.
    reports: bool = True
    # See the module docstring. 0 means "anything in the pool will do".
    min_context: int = 0
    # Both are assigned per instance by the generated __init__, so they stay
    # plain functions rather than becoming bound methods.
    report: Callable = report_from_text
    # None means this node contributes nothing beyond the note every node
    # contributes; see `absorb` below.
    absorb: Optional[Callable] = None


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
class NodeResult:
    text: str
    outputs: list
    # The same tool calls with their arguments kept. `outputs` is enough to
    # decide what happened; `calls` is what lets a session say *which command*
    # produced an exit code, which is the substrate a rationale has to cite.
    calls: list = field(default_factory=list)
    # The exact prompt this node was handed, before any tool call. Without it a
    # post-mortem can see that a node went wrong but not whether it was given
    # what it needed -- and in a design whose whole mechanism is the
    # orchestrator curating context downward, that is the question worth
    # asking. The system prompt is node-static and lives in the node's own
    # file; only this half varies per step.
    prompt: str = ""


def run_node(model, node: Node, toolset, log: Log, config: dict,
             stats: Stats, force_summary: bool = False, brief=None) -> NodeResult:
    """Run one node to completion and throw away its conversation.

    Only the return value reaches the log. The messages built here are local to
    the call, which is what keeps step 40 as cheap as step 1.

    `force_summary` spends the final round with no tools bound. A node that
    used every round on tool calls otherwise ends holding a tool-calling
    response, whose text is empty -- so it does all the work and reports
    nothing, and the next node reads a log with nothing in it.

    `brief` replaces the node's static instruction with one written for this
    step by the orchestrator (agent/harness/protocol.py). The node still sees
    only the log entries it declared, so the brief adds context rather than
    replacing the budget that bounds it.
    """
    # A node that declares a context floor is asking to be routed to a
    # wide-context member. Applied before bind_tools so the copy carries both.
    if node.min_context and hasattr(model, "for_context"):
        model = model.for_context(node.min_context)

    tools = [toolset[name] for name in node.tools if name in toolset]
    bound = model.bind_tools(tools) if tools else model
    # Only worth forcing on a node whose value is what it says, and only when
    # there is a round to spare. On an acting node it steals the round it
    # needed to act (see Node.reports).
    forcing = force_summary and node.reports and tools and node.max_rounds >= 2

    ask = brief.render() if brief is not None else f"# Your job\n{node.instruction}"
    # What the node knows, then what it is being asked. The log entries arrive
    # as their own messages rather than as one rendered blob, so a node's
    # context reads as the ordered record it is.
    messages = [SystemMessage(content=node.prompt),
                *log.view(node.reads),
                HumanMessage(content=ask)]
    # Captured before the tool rounds append to `messages`; this is the half a
    # post-mortem needs, and it is the only half that varies per step.
    given = "\n\n".join(str(m.content) for m in messages[1:])

    outputs, calls_made, text = [], [], ""
    for round_no in range(node.max_rounds):
        final = round_no == node.max_rounds - 1
        if forcing and final:
            messages.append(HumanMessage(
                content="No more tool calls. Answer now, in plain text."))
            stats.add(node.name, estimate_tokens(messages, None))
            text = _text_of(model.invoke(messages, config=config))
            break

        stats.add(node.name, estimate_tokens(messages, tools))
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

    return NodeResult(text=text, outputs=outputs, calls=calls_made, prompt=given)


def absorb(node: Node, report, result: NodeResult, log: Log) -> None:
    """Append a finished step to the log. The only channel between nodes.

    The finding and the step line are every node's contribution. Anything more
    is that node's own business, and lives in that node's file.
    """
    if report.finding:
        log.note(node.name, report.finding)
    if node.absorb is not None:
        node.absorb(result, log)
    log.step(node.name, f"{report.status}: {report.finding[:80]}")


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
