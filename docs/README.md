# free_coding_agent — wiki

A router that pools free-tier LLM accounts behind one interface, the agents
that run on it, and an evaluation harness that decides whether a change to
either actually helped.

**This wiki is the primary way to understand the repository.** It explains the
logic and the reasoning, not the code — read a page instead of reading the
source, and read the source only when you're about to change it.

New here? Read [Overview](overview.md), then [Status and roadmap](status.md).

## Index

### Start here

| Page | What it answers |
| --- | --- |
| [Overview](overview.md) | What the project is for, what it refuses to become, and what lives where |
| [Status and roadmap](status.md) | Where things stand, what is blocked, what to do next, what is settled ⟳ *changes often* |

### The pool — `llm_router/`

| Page | What it answers |
| --- | --- |
| [The pool model](pool/model.md) | The vocabulary: accounts × models, priority tiers, pull-based availability |
| [Failover](pool/failover.md) | How a request finds a working account: size-aware selection, failure classification, cooldown |
| [Providers and limits](pool/providers.md) | Getting keys, the config schema, current free-tier limits, adding a model or platform |
| [Quota panel](pool/quota.md) | What the pool has spent, and how close each account is to its wall |

### The agents — `agent/`

| Page | What it answers |
| --- | --- |
| [The coding agent](agents/code.md) | One conversation over the pool and one jail, configured the way `deepagents-code` is |
| [The web explorer](agents/explore.md) | The agent that reads the web and writes notes the coding agent can use |
| [The improvement agent](agents/improve.md) | Reads the other agents' runs, names what recurs, delegates the fix, checks it stopped |
| [Delegation](agents/delegation.md) | How one agent asks another for work: it runs the command |

### Evaluation — `evals/`

| Page | What it answers |
| --- | --- |
| [Evaluation method](evaluation/method.md) | How a change gets decided: configurations, fair comparison, the promotion rule |
| [Scenarios](evaluation/scenarios.md) | What a test case is, and why the answers are hidden |
| [Metrics](evaluation/metrics.md) | What gets measured, and why nothing is collapsed into one score |
| [Probes](evaluation/probes.md) | The small tests: one agent, one situation, one decision |
| [Changing how an agent behaves](evaluation/changing-behaviour.md) | The test-first loop for a behaviour change, with probes as the instrument |
| [Observability](evaluation/observability.md) | What a run leaves behind: the trace, the run tree, the condensed record |

### Operations

| Page | What it answers |
| --- | --- |
| [Serving the agents](operations/serving.md) | The agents as HTTP endpoints, in a container |
| [Deployment](operations/deployment.md) | Where this can run unattended, and why most hosting platforms cannot |
| [Driving the free agents](operations/driving-agents.md) | Handing a brief to an agent from the command line |

### Design notes — [design/](design/)

Proposals and working notes, not descriptions of what exists. They go stale by
design; what they settle moves into a page above.

| Note | What it is |
| --- | --- |
| [long-run-harness](design/long-run-harness.md) | The standing maintainer: the daily cycle and what it requires |
| [generative-scenarios](design/generative-scenarios.md) | Evaluating "build X" requests, not just repairs |
| [next-steps](design/next-steps.md) | Ideas to come back to |

## Documents outside this wiki

| Where | What | Why it's not a wiki page |
| --- | --- | --- |
| [CLAUDE.md](../CLAUDE.md), [AGENTS.md](../AGENTS.md) | Agent entry point (Claude Code, Codex): north star, standards, layout | Loaded into every agent's context; must stay short |
| [README.md](../README.md) | Human entry point: quick start, links out | Same reason |
| [evals/CONFIGS.md](../evals/CONFIGS.md) | Ledger of every configuration tried and its verdict | Append-only data, next to the results it indexes |
| `.claude/artifacts/`, `research/` | Plans, audits, one-off investigations | Point-in-time, read once, not maintained |

## Conventions

- **Grouped by topic, not numbered.** A page lives in the folder of the
  subsystem it explains; the index above is the reading order. Adding a page
  means adding an index row. Headings carry no section numbers, so adding one
  never renumbers anything — link to a section by its heading's anchor.
- **One topic per page.** Split a page when it starts covering two unrelated
  things rather than letting it grow.
- **Concepts, not code.** Explain the logic and the reasoning behind non-obvious
  choices. Link to source files for the *what*; the page carries the *why*.
- **Terse.** These pages are read by agents on a token budget. A bloated page
  defeats its own purpose.
- **State lives on one page.** [Status and roadmap](status.md) is the one page
  expected to change often; every other page describes design. A measurement
  that stops being current moves out of the design pages, not into them.
- Detail belongs here, not in [CLAUDE.md](../CLAUDE.md) or
  [README.md](../README.md) — both stay thin entry points that link in.

## Maintenance

A `Stop` hook in [.claude/settings.json](../.claude/settings.json) runs a Haiku
agent after every turn. If every changed path is inside `docs/`, or is the root
`README.md` or `CLAUDE.md`, it does nothing — docs never edit in response to
docs. Otherwise it reads this index, checks the non-doc changes against the
pages, and makes a minimal, factual edit to whichever went stale, including
adding an index row when a page is added. This index is the contract it reads:
keep it accurate or the automation degrades.
