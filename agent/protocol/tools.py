"""The one tool an agent needs to reach another agent.

One tool, not a family. `delegate` is A2A `message/send` and nothing else:
discovery is prose in the system prompt
([registry.directory_section](registry.py)) and `tasks/get` has no local caller,
because a blocking send already returns the task it would fetch. Both are
methods the transport exposes and neither is worth a schema on every step
([6.4](../../docs/06-agent.md#64-why-it-is-shaped-this-way)).

The description is written to the same standard as the rest of this repo's
tools: it promises only what the backend does, because a tool advertising
capabilities it does not have is a measured cause of failed calls
([agent/runtime/tools.py](../runtime/tools.py)). So it says the call is slow and
expensive, and that the answer arrives as files.
"""

from __future__ import annotations

from agent.protocol.local import LocalTransport, render
from agent.protocol.types import Message


def make_delegate_tool(transport: LocalTransport):
    """The `delegate` StructuredTool bound to one transport."""
    from langchain_core.tools import StructuredTool

    names = ", ".join(f"`{n}`" for n in transport.registry.names)

    def delegate(agent: str, request: str) -> str:
        if not request.strip():
            return "error: the request is empty."
        task = transport.message_send(agent, Message.user(request.strip()))
        return render(task, agent)

    delegate.__doc__ = (
        f"Ask another agent to do a job and wait for its report. Agents: {names}.\n\n"
        "This runs a whole agent session, so it is slow and spends the shared "
        "free-tier budget — one well-specified request beats three vague ones. "
        "Write the request as a brief for someone who cannot see your "
        "conversation: state the question, what the answer is for, and what "
        "would make it useless. The result names the files it wrote; read those "
        "for the detail."
    )

    return StructuredTool.from_function(
        func=delegate, name="delegate", description=delegate.__doc__)
