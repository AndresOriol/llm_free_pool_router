# free_coding_agent — wiki

A router that pools multiple free-tier LLM accounts behind one interface, an
agent that runs on it, and an evaluation harness that decides whether changes to
either actually helped.

**This wiki is the primary way to understand the repository.** It explains the
logic and the reasoning, not the code — read a page instead of reading the
source, and read the source only when you're about to change it.

New here? Start with [1. Overview](01-overview.md), then
[2. Repo map](02-repo-map.md).

## Where things stand right now

*The three facts most likely to mislead someone picking this up cold. Everything
else on this page is design and changes rarely; this block is state.*

- **Active work lives on the `harness/adhoc-router` branch, not `master`.** It
  holds an alternative agent architecture — same task for 39× fewer tokens, no
  demonstrated correctness gain, deliberately unmerged
  ([6.12](06-agent.md#612-an-alternative-architecture-the-ad-hoc-role-harness)).
- **The one L0 scenario is exhausted as a measuring instrument.** Seven
  configurations were run against it; none could be distinguished from another,
  and one scored 3/3 and 1/3 on consecutive batches. Re-running them will
  produce a different random ordering, not an answer
  ([6.14.1](06-agent.md#6141-the-pass-column-is-noise-and-i-can-prove-it)).
- **Authoring L1/L2 scenarios is the single blocking item**
  ([13.2](13-roadmap.md#132-what-to-do-next)). Every measured failure so far was
  `reasoning`, never `retrieval` or `tooling`, which is why more architecture
  work is not the next move
  ([6.14.2](06-agent.md#6142-every-failure-is-reasoning-and-that-reframes-the-whole-exercise)).

---

## Index

### Part I — Orientation

**[1. Overview](01-overview.md)** — what the project is for, and what it refuses to become
&nbsp;&nbsp;&nbsp;&nbsp;[1.1](01-overview.md#11-the-goal) The goal ·
[1.2](01-overview.md#12-why-this-is-infrastructure-not-a-demo) Why this is infrastructure ·
[1.3](01-overview.md#13-the-three-subsystems) The three subsystems ·
[1.4](01-overview.md#14-what-is-deliberately-not-built) What is deliberately not built ·
[1.5](01-overview.md#15-where-the-project-actually-stands) Where it actually stands ·
[1.6](01-overview.md#16-reading-paths) Reading paths

**[2. Repo map](02-repo-map.md)** — what lives where, and the question each file answers
&nbsp;&nbsp;&nbsp;&nbsp;[2.2](02-repo-map.md#22-llm_router--the-pool) `llm_router/` ·
[2.3](02-repo-map.md#23-agent--the-coding-agent) `agent/` ·
[2.4](02-repo-map.md#24-evals--the-measurement-harness) `evals/` ·
[2.6](02-repo-map.md#26-related-repos) Related repos ·
[2.7](02-repo-map.md#27-state-that-lives-outside-git) State outside git

### Part II — The pool

**[3. The pool model](03-pool-model.md)** — the vocabulary everything else assumes
&nbsp;&nbsp;&nbsp;&nbsp;[3.1](03-pool-model.md#31-vocabulary) Vocabulary ·
[3.2](03-pool-model.md#32-why-the-atom-is-account--model-not-account) Why the atom is account × model ·
[3.3](03-pool-model.md#33-the-fan-out) The fan-out ·
[3.4](03-pool-model.md#34-priority-tiers) Priority tiers, and the finding behind them ·
[3.5](03-pool-model.md#35-availability-is-pull-based) Availability is pull-based ·
[3.6](03-pool-model.md#36-every-pool-member-must-support-tool-calling) Tool calling is mandatory

**[4. Failover](04-failover.md)** — the core logic: how a request finds a working account
&nbsp;&nbsp;&nbsp;&nbsp;[4.1](04-failover.md#41-the-lifecycle-of-one-request) Lifecycle of one request ·
[4.2](04-failover.md#42-size-aware-selection) Size-aware selection ·
[4.3](04-failover.md#43-classifying-a-failure) Classifying a failure ·
[4.4](04-failover.md#44-cooldown-and-backoff) Cooldown and backoff ·
[4.5](04-failover.md#45-the-failover-loop) The failover loop ·
[4.6](04-failover.md#46-known-gaps) Known gaps ·
[4.7](04-failover.md#47-making-a-reroute-visible) Making a reroute visible

**[5. Providers and limits](05-providers.md)** — the free-tier landscape, and how to grow the pool
&nbsp;&nbsp;&nbsp;&nbsp;[5.1](05-providers.md#51-getting-keys) Getting keys ·
[5.2](05-providers.md#52-config-schema) Config schema ·
[5.3](05-providers.md#53-why-max_input_tokens-matters) Why `max_input_tokens` matters ·
[5.4](05-providers.md#54-current-free-tier-limits) Current limits ·
[5.5](05-providers.md#55-adding-a-model-or-account) Adding a model or account ·
[5.6](05-providers.md#56-adding-a-new-platform) Adding a new platform

### Part III — The agent

**[6. The coding agent](06-agent.md)** — what it can do, what it costs per step, and how to make it better
&nbsp;&nbsp;&nbsp;&nbsp;[6.1](06-agent.md#61-what-it-is) What it is ·
[6.2](06-agent.md#62-the-blast-radius) The blast radius ·
[6.3](06-agent.md#63-the-agents-instructions) The agent's instructions ·
[6.4](06-agent.md#64-what-the-model-actually-receives) What the model actually receives ·
[6.5](06-agent.md#65-the-librarys-prompts-and-what-they-cost-us) The library's prompts, and what they cost ·
[6.6](06-agent.md#66-loop-budget) Loop budget ·
[6.7](06-agent.md#67-what-failover-looks-like-in-practice) Failover in practice
&nbsp;&nbsp;&nbsp;&nbsp;⚠ *proposals under review:*
[6.8](06-agent.md#68-why-the-agent-underperforms-the-measured-diagnosis) The measured diagnosis ·
[6.9](06-agent.md#69-proposed-strategies) Proposed strategies ·
[6.10](06-agent.md#610-repo-level-configuration) Repo-level configuration ·
[6.11](06-agent.md#611-proposed-baseline-implementation) Proposed baseline implementation
&nbsp;&nbsp;&nbsp;&nbsp;⚙ *built on a branch:*
[6.12](06-agent.md#612-an-alternative-architecture-the-ad-hoc-role-harness) The ad-hoc role harness ·
[6.13](06-agent.md#613-what-the-comparison-actually-showed) What the comparison showed ·
[6.14](06-agent.md#614-architecture-variants-tried) Architecture variants tried

**[7. Observability](07-observability.md)** — two traces, deliberately
&nbsp;&nbsp;&nbsp;&nbsp;[7.1](07-observability.md#71-why-two) Why two ·
[7.2](07-observability.md#72-langsmith) LangSmith ·
[7.3](07-observability.md#73-the-local-trace) The local trace ·
[7.4](07-observability.md#74-the-shape-is-a-contract) The shape is a contract ·
[7.5](07-observability.md#75-reading-routing-decisions-live) Reading routing decisions live

### Part IV — Evaluation

**[8. Evaluation method](08-evaluation-method.md)** — how a change gets decided
&nbsp;&nbsp;&nbsp;&nbsp;[8.1](08-evaluation-method.md#81-the-premise) The premise ·
[8.2](08-evaluation-method.md#82-vocabulary) Vocabulary ·
[8.3](08-evaluation-method.md#83-where-things-live) Where things live ·
[8.4](08-evaluation-method.md#84-what-a-configuration-is) What a configuration is ·
[8.5](08-evaluation-method.md#85-the-run-lifecycle) The run lifecycle ·
[8.6](08-evaluation-method.md#86-fair-comparison) Fair comparison ·
[8.7](08-evaluation-method.md#87-the-promotion-rule) The promotion rule ·
[8.8](08-evaluation-method.md#88-suites-and-selective-running) Suites and selective running ·
[8.9](08-evaluation-method.md#89-budget) Budget ·
[8.10](08-evaluation-method.md#810-prior-art-and-why-we-still-build) Prior art

**[9. Scenarios](09-scenarios.md)** — what a test case is, and why the answers are hidden
&nbsp;&nbsp;&nbsp;&nbsp;[9.1](09-scenarios.md#91-storage-one-branch-per-topic-one-commit-per-scenario) Storage ·
[9.2](09-scenarios.md#92-materialization-is-git-archive-not-a-checkout) Materialization ·
[9.3](09-scenarios.md#93-anatomy) Anatomy ·
[9.4](09-scenarios.md#94-scenarioyaml) `scenario.yaml` ·
[9.5](09-scenarios.md#95-the-task-file) The task file ·
[9.6](09-scenarios.md#96-categories-to-cover) Categories ·
[9.7](09-scenarios.md#97-the-difficulty-ladder) The difficulty ladder ·
[9.8](09-scenarios.md#98-the-validation-gate) The validation gate

**[10. Metrics](10-metrics.md)** — what gets measured, and why nothing is collapsed into one score
&nbsp;&nbsp;&nbsp;&nbsp;[10.1](10-metrics.md#101-the-axes) The axes ·
[10.2](10-metrics.md#102-automatic-metrics) Automatic metrics ·
[10.3](10-metrics.md#103-failure-taxonomy) Failure taxonomy ·
[10.4](10-metrics.md#104-the-judge) The judge ·
[10.5](10-metrics.md#105-ranking-is-lexicographic-not-weighted) Lexicographic ranking ·
[10.6](10-metrics.md#106-what-a-run-leaves-behind) What a run leaves behind

**[11. Evaluation status](11-eval-status.md)** — the running state ⟳ *changes often*
&nbsp;&nbsp;&nbsp;&nbsp;[11.2](11-eval-status.md#112-whats-built) What's built ·
[11.3](11-eval-status.md#113-where-the-numbers-stand) Where the numbers stand ·
[11.4](11-eval-status.md#114-blockers) Blockers ·
[11.5](11-eval-status.md#115-what-to-do-next) What to do next

### Part V — How the repo evolves

**[12. Development harness](12-development-harness.md)** — how this repo gets built, by agents
&nbsp;&nbsp;&nbsp;&nbsp;[12.2](12-development-harness.md#122-model-tiers) Model tiers ·
[12.3](12-development-harness.md#123-coding-standards) Coding standards ·
[12.4](12-development-harness.md#124-how-the-docs-stay-current) How the docs stay current ·
[12.5](12-development-harness.md#125-driving-the-free-agents) Driving the free agents ·
[12.6](12-development-harness.md#126-commits) Commits ·
[12.7](12-development-harness.md#127-the-rule-that-governs-changes-to-the-harness) The rule governing harness changes

**[13. Roadmap and scope](13-roadmap.md)** — where this goes next, and what's already settled
&nbsp;&nbsp;&nbsp;&nbsp;[13.1](13-roadmap.md#131-the-two-phases-of-the-project) The two phases ·
[13.2](13-roadmap.md#132-what-to-do-next) What to do next ·
[13.3](13-roadmap.md#133-known-constraints-that-shape-the-roadmap) Known constraints ·
[13.4](13-roadmap.md#134-open-questions) Open questions ·
[13.5](13-roadmap.md#135-settled-decisions) Settled decisions ·
[13.6](13-roadmap.md#136-explicitly-out-of-scope) Out of scope ·
[13.7](13-roadmap.md#137-how-to-propose-a-change) How to propose a change

---

## Documents outside this wiki

| Where | What | Why it's not a wiki page |
| --- | --- | --- |
| [CLAUDE.md](../CLAUDE.md) | Agent entry point: north star, standards, index | Loaded into every agent's context; must stay short |
| [README.md](../README.md) | Human entry point: quick start, links out | Same reason |
| [evals/CONFIGS.md](../evals/CONFIGS.md) | Ledger of every configuration tried and its verdict | Append-only data, lives next to the results it indexes |
| [.claude/reports/](../.claude/reports/) | One-off deep investigations, kept verbatim | Point-in-time research, not maintained state |

## Conventions

- **One topic per page.** Split a page when it starts covering two unrelated
  things rather than letting it grow.
- **Pages are numbered and flat.** The number is the reading order; the index
  above is the contract. Adding a page means adding an index entry.
- **Concepts, not code.** Explain the logic and the reasoning behind non-obvious
  choices. Link to source files for the *what*; the page carries the *why*.
- **Terse.** These pages are read by agents on a token budget. A bloated page
  defeats its own purpose.
- Detail belongs here, not in [CLAUDE.md](../CLAUDE.md) or
  [README.md](../README.md) — both stay thin entry points that link in.
- [11. Evaluation status](11-eval-status.md) is the one page expected to change
  often. Everything else describes design and changes rarely.

## Maintenance

The documentation tier keeps this wiki current via the `Stop` hook in
[.claude/settings.json](../.claude/settings.json): after each turn it checks
non-doc changes against these pages and updates whatever went stale, including
adding pages and index entries as components are added. It never edits in
response to changes made within `docs/` itself. See
[12.4](12-development-harness.md#124-how-the-docs-stay-current).
