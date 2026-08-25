"""A coding harness: many narrow agents instead of one wide one.

A conversational agent hands every step the same tools and the whole history,
which costs more than two members of this pool can accept at all. This harness
takes the opposite trade: more turns, each one small enough that the whole pool
can serve it.

Five things, and each is a folder or a file you can read on its own:

- **log.py** -- everything the session knows, as one ordered list of entries.
  Nodes append to it; a node is handed the kinds it declared and nothing else.
- **nodes/** -- one file per node: its prompt, its tools, the kinds of log entry
  it may read, how its result is judged, and what it appends. A node's prompt is
  bounded by what that file declares rather than by run length.
- **graph.py** -- the edges between nodes, and the refusals the code applies to
  the orchestrator's choice. Running the code after an edit needs no
  intelligence, so it spends no tokens.
- **record/** -- what survives the session: the commits, the journal, the
  transcript, the rationale a human reads.
- **session.py** -- one run, start to finish, wiring those four together.

The substrate they run on -- the pooled model, the filesystem jail, the tools,
the trace -- is agent/runtime/, and knows nothing about any of this.
"""

from agent.harness.log import Log
from agent.harness.session import run_session

__all__ = ["Log", "run_session"]
