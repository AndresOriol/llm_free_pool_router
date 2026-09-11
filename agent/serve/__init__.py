"""The agents as HTTP endpoints, for running this harness in a container.

Four files, one job each:

    workspace.py  what an agent is pointed at, and why a name is not a path
    task.py       one served run: what was asked, and what came back
    runner.py     the queue, and the single worker that runs one at a time
    app.py        http.server, and nothing else

Nothing here changes an agent, and nothing here *is* an agent. The worker runs
`python -m agent.<name> <workdir> --task "..."` -- the same command a person
types -- so a served run and a local one are the same run
([18. Serving](../../docs/18-serving.md),
[16. Delegation](../../docs/16-delegation.md)).
"""

from agent.serve.workspace import BadWorkspace

__all__ = ["BadWorkspace"]
