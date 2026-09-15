"""What every agent needs and none of them owns: the model, the trace, the prompts.

Nothing here knows what a session is. `RouterChatModel` fronts the pool,
`pool.connect` holds the model to a context floor, `prompts` is what every agent
is told about where it runs, and `JsonlTracer` and `run_tree` record what every
call cost and what the run did. `agent/code`, `agent/explore` and
`agent/improve` are its consumers; the router's own smoke test is another.

The backends are deepagents' own: `LocalShellBackend` where an agent runs
commands, `FilesystemBackend` where it only reads and writes
([6.2](../../docs/06-agent.md#62-the-blast-radius)).
"""

from agent.utils import file_tools  # noqa: F401 - read_file reads whole files
from agent.utils.chat_model import RouterChatModel
from agent.utils.trace import JsonlTracer, tracer_from_env

__all__ = ["RouterChatModel", "JsonlTracer", "tracer_from_env"]
