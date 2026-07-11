# Agentic Development Harness

This project is built by autonomous coding agents, not by a single model doing
everything. The harness exists to get high implementation quality at minimal
token cost. That means: expensive models think, cheap models type, and no
model reads more than it needs to.

## Model tiers

### Design tier (Opus) — orchestrator

- Never touches code directly except to skim, not to edit. Reads
  [CLAUDE.md](../CLAUDE.md), this file, and curated docs — not source.
- Owns: what gets built next, how many sub-agents to spawn, which tier each
  sub-agent runs at, and the intermediate goals each one is given.
- Owns design decisions and keeps the overall logic simple. If a plan is
  getting complicated, that's this tier's problem to catch, not the
  implementer's.
- Every decision must trace back to the North Star goal in
  [CLAUDE.md](../CLAUDE.md). If a proposed task doesn't clearly serve "keep
  the free token pool available," push back on it rather than build it.
- Delegates implementation with a scoped, unambiguous brief — file paths,
  the interface to implement, the intermediate goal — not "improve the
  router."

### Implementation tier (Sonnet) — builders

- Reads and understands orchestrator briefs plus the curated documentation
  (not the whole codebase from scratch) to implement changes.
- Responsible for correctness of the actual code change: new providers,
  router behavior, retry/cooldown logic, tests.
- When a brief is ambiguous or conflicts with what the code actually does,
  surface that back up rather than guessing — the design tier owns
  resolving ambiguity, not this tier.
- Runs the `ponytail` skill on non-trivial changes to keep implementations
  minimal.

### Documentation tier (Haiku) — maintainer

- Cheapest tier, narrowest job: keep curated documentation accurate and
  current after implementation changes land (e.g. provider list, config
  schema, known failure modes of each provider).
- Does not make design or implementation decisions. Its output is what the
  Sonnet tier reads instead of re-deriving context from source every time.
- Should keep docs terse — the point is to save the next tier's tokens, so
  a bloated doc defeats the purpose.

## Why this split

Reading and reasoning over code is the expensive part. Push it down: Opus
reasons over docs and goals only, Sonnet reasons over docs plus the specific
files it's editing, Haiku reasons over diffs to update docs. Nobody re-reads
the whole repo per task.

## Skills

Use skills where they measurably improve quality-per-token instead of adding
ceremony:

- **ponytail** — apply before/during implementation to keep solutions minimal
  (no speculative abstraction, no unused flexibility, standard library over
  new dependencies). This is the primary quality lever for the
  implementation tier since it directly fights the failure mode of an agent
  over-building.
- Prefer other skills only when they clearly reduce total tokens spent or
  catch a real defect class (e.g. code-review before merging orchestrator
  output) — don't add skill invocations as a default habit.

## Open questions (for the orchestrator to resolve as they come up, not to
block on now)

- How provider "availability" and cooldown state get tracked/shared across
  runs (currently in-memory per process, per [llm_router/base_provider.py](../llm_router/base_provider.py)).
- Where curated docs for the Sonnet/Haiku tiers actually live once the
  provider list grows (likely a docs file per provider or a generated
  table — Haiku tier's call to maintain).
