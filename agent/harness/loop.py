"""The state machine: deterministic where it can be, LLM-driven where it must be.

The transitions below are fixed Python, not model decisions. That is the point
of the design: every edge the model does not get to choose is an LLM call not
spent, and a way the run cannot go wrong. The model is consulted for exactly two
things -- doing the work inside a role, and choosing what to do after a test
fails.

    locate -> inspect -> edit -> [test] -> pass? done
                 ^         ^                 |
                 |         |              fail -> route -> {edit, inspect, locate, giveup}
                 +---------+-------------------------+

`[test]` runs no model at all: "run the tests after an edit" needs no
intelligence, so it costs no tokens.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from llm_router.base_provider import estimate_tokens
from agent.harness.blackboard import Blackboard
from agent.harness.roles import ROLES, ROUTE_CHOICES

logger = logging.getLogger("harness")

DEFAULT_TEST_CMD = "python -m pytest"
MAX_CYCLES = 6
# Paths as the tools emit them: /pkg/mod.py, optionally followed by :line:
_PATH_RE = re.compile(r"(/[\w./\-]+\.\w+)")


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


def run_role(model, role, toolset, bb: Blackboard, config: dict,
             stats: Stats) -> RoleResult:
    """Run one role to completion and throw away its conversation.

    Only the return value reaches the blackboard. The messages built here are
    local to the call, which is what keeps step 40 as cheap as step 1.
    """
    tools = [toolset[name] for name in role.tools if name in toolset]
    bound = model.bind_tools(tools) if tools else model

    messages = [
        SystemMessage(content=role.prompt),
        HumanMessage(content=f"{bb.render(role.sections)}\n\n# Your job\n{role.instruction}"),
    ]

    outputs, text = [], ""
    for _ in range(role.max_rounds):
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
            messages.append(ToolMessage(content=out, tool_call_id=call.get("id", "")))

    return RoleResult(text=text, outputs=outputs)


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


def run_tests(backend, command: str = DEFAULT_TEST_CMD):
    """Deterministic step -- no model. Returns (output, passed)."""
    result = backend.execute(command)
    output = f"exit={result.exit_code}\n{result.output}"
    return output, result.exit_code == 0


def solve(model, backend, toolset, task: str, config=None,
          test_cmd: str = DEFAULT_TEST_CMD, max_cycles: int = MAX_CYCLES):
    """Drive one task to a verdict. Returns (blackboard, stats, outcome)."""
    config = config or {}
    bb = Blackboard(task=task)
    stats = Stats()
    state = "locate"

    while bb.cycles < max_cycles:
        if state == "locate":
            result = run_role(model, ROLES["locate"], toolset, bb, config, stats)
            bb.add_files(_harvest_paths(result))
            bb.record("locate", ", ".join(bb.files[:4]) or "nothing found")
            state = "inspect"

        elif state == "inspect":
            result = run_role(model, ROLES["inspect"], toolset, bb, config, stats)
            bb.add_files(_harvest_paths(result))
            if result.text:
                bb.add_note(result.text)
            bb.record("inspect", result.text or "no finding")
            state = "edit"

        elif state == "edit":
            result = run_role(model, ROLES["edit"], toolset, bb, config, stats)
            applied = [out for _, out in result.outputs if out.startswith("ok:")]
            for line in applied:
                bb.add_edit(line)
            bb.record("edit", applied[-1] if applied
                      else "no edit applied: " + (result.text[:80] or "?"))
            state = "test"

        elif state == "test":
            # Deterministic: no model call, no tokens.
            output, passed = run_tests(backend, test_cmd)
            bb.set_test(output, passed)
            bb.record("test", "passed" if passed else "failed")
            if passed:
                return bb, stats, "pass"
            bb.cycles += 1
            state = "route"

        elif state == "route":
            result = run_role(model, ROLES["route"], toolset, bb, config, stats)
            state = _parse_route(result.text, bb)
            bb.record("route", f"-> {state}")
            if state == "giveup":
                return bb, stats, "giveup"

    return bb, stats, "exhausted"


def _harvest_paths(result: RoleResult) -> list:
    """Pull file paths out of tool output rather than asking the model to list
    them. Small models format prose unreliably; the tools' output shape is
    fixed, so reading it directly removes a whole class of failure."""
    found = []
    for _, out in result.outputs:
        found.extend(_PATH_RE.findall(out))
    return found


def _parse_route(text: str, bb: Blackboard) -> str:
    """Map the router's word to a state, tolerating small-model chattiness.

    Falls back to `edit` when nothing parses: after a failing test the most
    common correct move is another edit, and a deterministic default is better
    than ending a run because a model wrote a sentence instead of a word.
    """
    upper = (text or "").upper()
    for word, state in ROUTE_CHOICES.items():
        if re.search(rf"\b{word}\b", upper):
            return state
    logger.warning("route returned no recognised choice (%r); defaulting to edit",
                   text[:80])
    return "edit"
