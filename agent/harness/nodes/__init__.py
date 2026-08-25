"""One node per file: prompt, tools, context budget, verdict, and what it hands on.

Everything about EXPLORE is in explore.py and nowhere else. That is the rule
this folder exists to enforce -- the previous shape spread one node across a
role table, a runner and three `if action ==` branches in the graph, so nobody
could read a node without reading all of them.

What is *not* here: the edges between nodes, and the refusals the code applies
to the orchestrator's choice. Those are relationships rather than nodes, and
they live in agent/harness/graph.py, which is the one place to read them.

The registry is written out rather than discovered by scanning: adding a node
should be a visible edit, and the graph builds its nodes from this dict.
"""

from agent.harness.nodes import document, execute, explore, orchestrate, review, write
from agent.harness.nodes.base import (COMMON, WIDE, Node, NodeResult, Stats,
                                      absorb, run_node)

# The hub. Deliberately absent from WORKERS: it is dispatched by the graph
# rather than chosen as a destination.
ORCHESTRATOR = orchestrate.NODE

WORKERS = {node.name: node for node in (explore.NODE, write.NODE, execute.NODE,
                                        document.NODE, review.NODE)}

__all__ = ["COMMON", "WIDE", "Node", "NodeResult", "ORCHESTRATOR", "Stats",
           "WORKERS", "absorb", "run_node"]
