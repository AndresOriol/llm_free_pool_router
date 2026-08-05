"""An ad-hoc coding harness: many narrow agents instead of one wide one.

The deep-agents loop hands every step the same nine tools, which costs ~6,100
tokens before the task is even read (docs/06-agent.md#64) -- more than two pool
members can accept at all, and most of the budget of the rest. This harness
takes the opposite trade: more turns, each one small enough that the whole pool
can serve it.

Each role sees one slice of a shared blackboard and holds one or two tools, so
a role's prompt is bounded by what it declares rather than by run length. The
transitions between roles are ordinary Python wherever the next move is
knowable -- running tests after an edit needs no intelligence, so it spends no
tokens.
"""

from agent.harness.blackboard import Blackboard
from agent.harness.loop import solve
from agent.harness.tools import make_tools

__all__ = ["Blackboard", "solve", "make_tools"]
