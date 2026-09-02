"""The agents as HTTP endpoints, for running this harness in a container.

Three files, one job each:

    workspace.py  what an agent is pointed at, and why a name is not a path
    runner.py     the pool, the queue, and the single worker that spends it
    app.py        A2A's methods over http.server, and nothing else

Nothing here changes an agent. The pool, the jail, the prompts, the failover and
the trace are the ones the CLI uses; this adds an address, a queue, and a
workspace to bind to ([18. Serving](../../docs/18-serving.md)).
"""

from agent.serve.workspace import BadWorkspace

__all__ = ["BadWorkspace"]
