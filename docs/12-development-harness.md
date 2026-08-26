[← Wiki index](README.md)

# 12. Development harness

*How this repo gets built. It is built by agents, which is why the process is
written down rather than assumed.*

## 12.1 The premise

This project is built by autonomous coding agents, not by one model doing
everything. The harness exists to get high implementation quality at minimal
token cost. Which means: **expensive models think, cheap models type, and no
model reads more than it needs to.**

Reading and reasoning over code is the expensive part, so push it down the
stack. Nobody re-reads the whole repo per task.

## 12.2 Model tiers

### Design tier (Opus) — orchestrator

- Reads [CLAUDE.md](../CLAUDE.md), this wiki, and curated docs — **not source**,
  except to skim.
- Owns: what gets built next, how many sub-agents to spawn, which tier each runs
  at, and the intermediate goal each is given.
- Owns design decisions, and owns keeping the overall logic simple. If a plan is
  getting complicated, that is this tier's problem to catch, not the
  implementer's.
- Every decision must trace back to the north star. If a proposed task doesn't
  clearly serve *"keep the free token pool available"*, push back rather than
  build it.
- Delegates with a scoped, unambiguous brief — file paths, the interface to
  implement, the intermediate goal. Not "improve the router".

### Implementation tier (Sonnet) — builders

- Reads the brief plus curated documentation, not the whole codebase from
  scratch.
- Owns correctness of the actual change: new providers, router behaviour,
  retry/cooldown logic, tests.
- When a brief is ambiguous or contradicts what the code actually does,
  **surface it upward** rather than guessing. Resolving ambiguity belongs to the
  design tier.
- Runs the `ponytail` skill on non-trivial changes to keep implementations
  minimal.

### Documentation tier (Haiku) — maintainer

- Cheapest tier, narrowest job: keep this wiki accurate after implementation
  changes land.
- Makes no design or implementation decisions. Its output is what the
  implementation tier reads instead of re-deriving context from source.
- Keeps docs **terse** — the point is to save the next tier's tokens, so a
  bloated doc defeats its own purpose.

## 12.3 Coding standards

- **Boring and reliable over clever.** This code needs to survive unattended for
  hours; prefer obvious control flow.
- New providers implement the `LLMProvider` interface. Never special-case a
  provider inside the router.
- **No secrets in code or config.** API keys are env vars only.
- Follow [ponytail](https://github.com/anthropics/skills) principles: the
  simplest solution that works, standard library over dependencies, no
  speculative abstraction. This is the primary quality lever for the
  implementation tier, because it directly fights an agent's dominant failure
  mode — over-building.
- Prefer other skills only when they clearly reduce total tokens spent or catch
  a real defect class. Don't add skill invocations as a default habit.

## 12.4 How the docs stay current

A `Stop` hook in [.claude/settings.json](../.claude/settings.json) runs a Haiku
agent after every turn. It diffs the repo, and:

- If **every** changed path is inside `docs/`, or is root `README.md` /
  `CLAUDE.md`, it does nothing. Docs never edit in response to docs.
- Otherwise it reads [docs/README.md](README.md) to learn how this wiki is
  organized, checks the non-doc changes against it, and makes a **minimal,
  factual** edit to whichever pages went stale — including updating the index
  when a page is added or removed.

This is why [docs/README.md](README.md) carries the conventions and the index:
it is the contract the maintenance tier reads. Keep it accurate or the
automation degrades.

## 12.5 Driving the free agents

The agents that build this project's sibling workloads are the free agents
themselves. The loop:

1. Write a scoped brief (Goal / Change / Constraints / Acceptance) into a file
   **inside the target workdir** — the agent is jailed there
   ([6.2](06-agent.md#62-the-blast-radius)).
2. Launch one-shot, pointing stdin at a one-line instruction:

   ```bash
   echo "Read /tasks/my-brief.md and do it" | python -m agent.deep <workdir>
   ```

   EOF makes the process exit, so this works as a background job.
3. The brief should tell the agent to write at `/` (its virtual root) and to run
   `python -m pytest` to check its own work.
4. The agent has no git. Review the diff, then commit.

The `closet_ai` sibling project exists partly as this loop's real workload —
every failure mode found while building it is input to this repo's roadmap
([2.6](02-repo-map.md#26-related-repos)).

## 12.6 Commits

- **No AI or agent attribution.** No `Co-Authored-By`, no mention of a model or
  agent having made the change. Commits read as if a human wrote them.
- Short imperative summary line. A body only when the "why" isn't obvious from
  the diff.
- **One topic per commit.** Cluster by topic, not by session — a docs change and
  an unrelated config change go in separate commits even if made back to back.

## 12.7 The rule that governs changes to the harness

Any change to the harness — router config, system prompt, backend, agent loop —
is a candidate configuration, not a decision. Branch it, add a config, run the
suite that covers it interleaved against baseline, apply the promotion rule, and
record the verdict in [evals/CONFIGS.md](../evals/CONFIGS.md) — **including for
changes that lost.** Knowing what didn't work is most of the value of keeping
the data, and it's what stops the same idea being re-tried every few months.

Full workflow: [8. Evaluation method](08-evaluation-method.md).

---

**Previous:** [← 11. Evaluation status](11-eval-status.md) · **Next:** [13. Roadmap and scope →](13-roadmap.md)
