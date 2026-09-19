"""The improvement agent: the one that works on the other agents.

`agent/code` changes a project. `agent/explore` reads the web. This one reads
what the *other two did* -- the runs they left behind -- and turns recurring
misbehaviour into a fix that someone else makes. It is modelled on LangSmith
Engine: detect a recurring failure, diagnose it against the source, have the
fix made, then track whether it stopped -- closing the issue when it did and
reopening it when it comes back
([The improvement agent](../../docs/agents/improve.md)).

- `agent.py` builds it -- settings, templating, model, the ledger, the
  read-only boundary, agent, and one run, top to bottom.
- `__main__.py` runs it: `python -m agent.improve . --task "..."`, or with no
  task, the standing pass.
- `prompts/` is what it is told: `system.md`, the standing pass, and the
  refusal a write gets. `tool_descriptions/` is what each tool is for.
- `tools.py` is what the tools do, over `records.py` (the evidence),
  `issues.py` (the ledger), `repo.py` (git) and `scenarios.py` (drafts).
"""
