"""The coding agent: a conversation on the pool.

`create_deep_agent` over a jailed backend, configured the way LangChain's
`deepagents-code` configures a coding agent
([The coding agent](../../docs/agents/code.md)).

- `agent.py` builds it -- settings, templating, model, project context, agent,
  and one run, top to bottom.
- `__main__.py` runs it: `python -m agent.code <workdir> --task "..."`.
- `prompts/` is everything it is told: `system.md`, the two sections a run can
  switch off, and the wrap-up. What every agent here is told about where it
  runs is in `agent/utils/prompts/`.
"""
