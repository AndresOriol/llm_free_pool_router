"""Refusing a command as a tool result, not as an exception.

Ported from `libs/code/deepagents_code/agent.py::ShellAllowListMiddleware` in
langchain-ai/deepagents (MIT), which exists there so a non-interactive run can
police the shell *without* human-in-the-loop interrupts -- an interrupt/resume
cycle splits one LangSmith run into several, and the record here is that run
tree (agent/utils/run_tree.py).

**This does not replace the backend's allowlist, and must not.**
`RestrictedShellBackend` is the boundary: it is what actually stops a command,
and it holds whether or not this middleware is installed. What this adds is the
*shape of the refusal*. The backend raises, and an exception surfaces to the
model as a failed tool call with whatever text the framework put on it; this
returns a `ToolMessage` that names the command, says why it was refused, and
lists what is allowed -- which the model can read and correct from on the next
step instead of retrying the same thing.

The distinction is the one already learned the expensive way: a run spent 900 of
its 1,050 seconds on heredocs that hung, because shell syntax was accepted
silently rather than refused with a reason
([6.2](../../docs/06-agent.md#62-the-blast-radius)). Being told is cheap.
"""

from __future__ import annotations

import logging
import shlex
from typing import Any, Awaitable, Callable, Optional, Sequence

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

logger = logging.getLogger("harness.code")


class ShellAllowListMiddleware(AgentMiddleware):
    """Reject an `execute` command that is not on the allowlist, as a message.

    Everything that is not an `execute` call passes straight through.
    """

    def __init__(self, allow_list: Sequence[str]) -> None:
        super().__init__()
        if not allow_list:
            raise ValueError(
                "allow_list must not be empty; run without the shell instead.")
        self._allow_list = list(allow_list)

    def _refusal(self, request) -> Optional[ToolMessage]:
        """The message to return instead of running this call, or None."""
        call = request.tool_call
        if call.get("name") != "execute":
            return None

        command = str((call.get("args") or {}).get("command", ""))
        problem = self._problem(command)
        if problem is None:
            return None

        logger.warning(f"Shell command rejected: {command!r} ({problem})")
        return ToolMessage(
            content=(f"Command rejected: `{command}`. {problem} "
                     f"Allowed programs: {', '.join(self._allow_list)}. "
                     f"Run one of those directly, or take another approach."),
            name="execute",
            tool_call_id=call["id"],
            status="error",
        )

    def _problem(self, command: str) -> Optional[str]:
        """Why this command cannot run, or None if it may.

        Mirrors `RestrictedShellBackend._refusal` so the model is told the same
        thing the backend would enforce a moment later -- the two disagreeing
        would be worse than either alone.
        """
        if not command.strip():
            return "It is empty."
        try:
            argv = shlex.split(command)
        except ValueError as exc:  # unbalanced quotes
            return f"It does not parse as a command ({exc})."
        if not argv:
            return "It is empty."

        program = argv[0]
        if "/" in program or "\\" in program:
            return (f"`{program}` names a path, and programs must be named "
                    f"bare.")
        if program not in self._allow_list:
            return f"`{program}` is not an allowed program."
        return None

    def wrap_tool_call(self, request, handler: Callable[[Any], Any]) -> Any:
        refusal = self._refusal(request)
        return refusal if refusal is not None else handler(request)

    async def awrap_tool_call(self, request,
                              handler: Callable[[Any], Awaitable[Any]]) -> Any:
        refusal = self._refusal(request)
        return refusal if refusal is not None else await handler(request)
