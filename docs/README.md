# Docs structure

This defines how `docs/` is organized for the whole project — not just the
current llm_router stage. As new components land (e.g. the deep-agents
integration in [CLAUDE.md](../CLAUDE.md)'s Phase 2), they get docs here too,
following the same rules.

## Two kinds of docs

1. **Harness docs** — how the project itself gets built: agent model tiers,
   skills, standards. Project-wide, stable, doesn't change with routine
   feature work. Currently: [ARCHITECTURE.md](ARCHITECTURE.md).
2. **Component docs** — how to set up or operate one specific part of the
   system, aimed at humans. One file per major component, named for what it
   covers (not for the doc's type). Currently:
   [PROVIDERS.md](PROVIDERS.md) (llm_router account setup). A future
   component (e.g. the deep-agents integration) gets its own file here, e.g.
   `DEEP_AGENTS.md`.

## Conventions

- One topic per file. Split a file when it starts covering two unrelated
  things rather than letting it grow.
- Every file lives flat in `docs/` (no subfolders yet — add them only if a
  component's docs genuinely outgrow a single file) and is listed in the
  index below.
- Component docs are named for the component/topic, in imperative form the
  reader recognizes (`PROVIDERS.md`, not `SETUP.md`).
- Keep [CLAUDE.md](../CLAUDE.md) and [README.md](../README.md) as thin
  entry points that link here — detail belongs in these files, not there.

## Index

| File | Scope | Covers |
| --- | --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | project-wide | agent model tiers, harness, skills |
| [PROVIDERS.md](PROVIDERS.md) | llm_router component | free-tier account signup & wiring |

## Maintenance

The documentation tier (Haiku, see ARCHITECTURE.md) keeps this directory
current via the `Stop` hook in [.claude/settings.json](../.claude/settings.json):
after each turn, it checks non-doc changes against these files and updates
them if something's now stale, including adding new files and index rows as
components are added. It never edits in response to changes made within
`docs/` itself.
