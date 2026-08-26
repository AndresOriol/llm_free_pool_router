"""The substrate an agent runs on: the model, the jail, the tools, the trace.

Nothing here knows what a session is. `RouterChatModel` fronts the pool,
`RestrictedShellBackend` is the filesystem/exec jail an agent is confined to,
`make_tools` is that backend's model-facing face, and `JsonlTracer` records
what every call cost. `agent/code` and `agent/explore` are its consumers; the
router's own smoke test is another.
"""

from agent.runtime.backend import RestrictedShellBackend
from agent.runtime.chat_model import RouterChatModel
from agent.runtime.tools import make_tools
from agent.runtime.trace import JsonlTracer, tracer_from_env

__all__ = ["RestrictedShellBackend", "RouterChatModel", "make_tools",
           "JsonlTracer", "tracer_from_env"]
