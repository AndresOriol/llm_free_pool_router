"""A coding harness: many narrow agents instead of one wide one.

A conversational agent hands every step the same tools and the whole history,
which costs more than two members of this pool can accept at all. This harness
takes the opposite trade: more turns, each one small enough that the whole pool
can serve it.

Each role sees one slice of a shared blackboard and holds one or two tools, so
a role's prompt is bounded by what it declares rather than by run length
(agent/harness/roles.py). The transitions between roles are ordinary edges in a
LangGraph state machine wherever the next move is knowable -- running the code
after an edit needs no intelligence, so it spends no tokens
(agent/harness/graph.py).
"""

from agent.harness.blackboard import Blackboard
from agent.harness.graph import run_session
from agent.harness.tools import make_tools

__all__ = ["Blackboard", "run_session", "make_tools"]
