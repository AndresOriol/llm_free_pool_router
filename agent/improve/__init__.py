"""The improvement agent: the one that works on the other agents.

`agent/code` changes a project. `agent/explore` reads the web. This one reads
what the *other two did* -- the runs they left behind -- and turns recurring
misbehaviour into a fix that someone else makes.

It is modelled on LangSmith Engine, which replaces the manual cycle of reading
traces, spotting a pattern and writing a fix with a loop that runs on its own:
detect a recurring failure in the recorded traces, diagnose it against the
source, propose the fix, then track whether it actually stopped happening --
closing the issue when it did and reopening it when it comes back
([19. The improvement agent](../../docs/19-improvement-agent.md)).

Two properties are load-bearing and both are inherited rather than invented:

1. **It does not edit the harness.** The fix is delegated to `agent/code` by
   running it, the same way the coding agent reaches the explorer. The
   agent that diagnoses is not the agent that changes the code, so the diff
   that lands is reviewable against a written diagnosis rather than being the
   only account of itself.
2. **An issue outlives the session that found it.** The ledger under
   `evals/results/issues/` is the memory: a named failure, its signature, the
   runs that showed it, what was done about it, and whether it came back. A
   pass that starts cold reads the ledger before it reads a single trace.
"""
