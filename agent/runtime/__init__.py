"""The substrate an agent runs on: the model, the jail, the tools, the trace.

Nothing here knows what a session is. `RouterChatModel` fronts the pool,
`RestrictedShellBackend` is the filesystem/exec jail an agent is confined to,
`make_tools` is that backend's model-facing face, `web.search` is a web
search that returns pages rather than snippets, `pool.connect` holds the model
to a context floor, `prompts` is what every agent is told about where it runs,
and `JsonlTracer` and `run_tree` record what every call cost and what the run
did. `agent/code`, `agent/explore` and `agent/improve` are its consumers; the
router's own smoke test is another.
"""

from agent.runtime.backend import RestrictedShellBackend
from agent.runtime.chat_model import RouterChatModel
from agent.runtime.tools import make_tools
from agent.runtime.trace import JsonlTracer, tracer_from_env

__all__ = ["RestrictedShellBackend", "RouterChatModel", "make_tools",
           "JsonlTracer", "tracer_from_env"]
