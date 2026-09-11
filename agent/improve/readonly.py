"""Refusing a write to the harness, as a tool result rather than an exception.

The improvement agent's one structural claim is that **the agent that diagnoses
is not the agent that changes the code**. That is what makes a diff reviewable
against a written diagnosis instead of being the only account of itself, and it
is worth exactly as much as it is enforced.

`create_deep_agent` installs `write_file` and `edit_file` over the backend, so
without this the rule lives only in the system prompt -- and a prompt is a
request. This makes it a boundary: an edit comes back as a `ToolMessage` naming
the tool, saying why, and pointing at the two tools that *are* the way to change
something. The shape is `ShellAllowListMiddleware`'s
([agent/runtime/shell.py](../runtime/shell.py)) and for the same reason: a refusal the
model can read and correct from costs one step, an exception costs the run.

The ledger is not an exception to this. `write_issue` writes it through Python,
not through the filesystem tools, so nothing here needs a hole in it.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

logger = logging.getLogger("harness.improve")

# The names deep agents give the tools that change a file. Listed rather than
# discovered, so a tool added upstream is refused only once someone has looked
# at it -- the opposite default would silently start refusing work.
WRITE_TOOLS = ("write_file", "edit_file", "str_replace", "apply_patch")

_WHY = (
    "This agent does not change the project. Diagnosing and fixing are "
    "deliberately different agents, so that a diff can be reviewed against a "
    "diagnosis that was written before it. Use `write_issue` to record what is "
    "wrong and `delegate_fix` to have the coding agent make the change."
)


class ReadOnlyMiddleware(AgentMiddleware):
    """Reject a filesystem write, as a message. Everything else passes."""

    def _refusal(self, request) -> Optional[ToolMessage]:
        call = request.tool_call
        name = call.get("name")
        if name not in WRITE_TOOLS:
            return None
        path = (call.get("args") or {}).get("file_path", "")
        logger.warning(f"Refused a write to {path!r} via {name}")
        return ToolMessage(
            content=f"`{name}` is not available here. {_WHY}",
            name=name, tool_call_id=call["id"], status="error")

    def wrap_tool_call(self, request, handler: Callable[[Any], Any]) -> Any:
        refusal = self._refusal(request)
        return refusal if refusal is not None else handler(request)

    async def awrap_tool_call(self, request,
                              handler: Callable[[Any], Awaitable[Any]]) -> Any:
        refusal = self._refusal(request)
        return refusal if refusal is not None else await handler(request)
