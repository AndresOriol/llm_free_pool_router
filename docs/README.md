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

- **There is one coding agent now, and its cost is unproven.** The narrow-role
  arm is deleted; `agent/code/` keeps a conversation on the pool's *widest*
  members (≥128,000 input tokens), configured the way `deepagents-code`
  configures one. That was a decision about what to maintain, **not** a finding:
  the last measurement had the deleted arm winning on input tokens 39× over, on
  inputs that no longer hold and that nobody has re-run
  ([6.1.1](06-agent.md#611-the-arm-that-was-deleted)). Watch `tokens_in`.
- **A second agent researches the web.** `agent/explore/` shares the loop, the
  pool and the jail, swaps the shell for `tavily_search`/`think_tool`, and hands
  off to the coding agent by writing files ([15](15-explorer.md)). It runs
  LangChain's deep-research workflow rather than a method written here, and its
  search returns the **page** rather than a summary of it — which is the fix for
  a run that searched 13 times and opened nothing
  ([15.8](15-explorer.md#158-the-deep-research-port)). Needs a Tavily key; the
  pool holds one.
- **The coding agent can now ask the explorer for research mid-task**, over
  A2A's data model on a local transport ([16](16-agent-protocol.md)). It is on
  by default and a delegation costs a whole explorer session, so a run's
  `tokens_in` may include one; the record carries `delegated_tasks` and
  `AGENT_PEERS=` turns it off. **Unmeasured** — `code-peers` has never been run
  against `code`.
- **The explorer has been watched once, and it drifted.** A live run searched 13 times, opened **zero** sources, and wrote one file at the end — all against its own prompt, and none of it visible to any existing metric. The prompt now carries numeric rules and
  [evals/research_trajectory.py](../evals/research_trajectory.py) checks them ([15.7](15-explorer.md#157-measured-against-a-reference-research-agent),
  [16.10](16-agent-protocol.md#1610-what-the-first-live-run-showed)).
- **The one L0 scenario is exhausted as a measuring instrument.** Seven
  configurations were run against it; none could be distinguished from another,
  and one scored 3/3 and 1/3 on consecutive batches. Re-running them will
  produce a different random ordering, not an answer
  ([6.4.2](06-agent.md#642-the-pass-column-is-noise)).
  There are now four more scenarios, at L1 and L2, and none has been run past
  n=2 ([11.2](11-eval-status.md#112-whats-built)).
- **A run's record is one object plus one flat file.** The agent fetches a
  single LangSmith run tree and writes it down; the `EVAL_TRACE_FILE` JSONL is
  still written alongside it, because every automatic metric is summed over that
  and it has to exist when LangSmith does not
  ([7.6](07-observability.md#76-the-record-one-run-tree)). This reverses
  "LangSmith is for watching, never for the record" — the expiry argument is
  answered by snapshotting the tree, not by rebuilding it by hand
  ([13.5](13-roadmap.md#135-settled-decisions)).

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
[4.2.1](04-failover.md#421-skipping-a-member-whose-day-is-spent) Skipping a spent day ·
[4.3](04-failover.md#43-classifying-a-failure) Classifying a failure ·
[4.3.1](04-failover.md#431-the-two-ways-a-member-dies-for-good) When a member dies for good ·
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

**[6. The coding agent](06-agent.md)** — one conversation over one pool and one jail, and the arm that was deleted to get there
&nbsp;&nbsp;&nbsp;&nbsp;[6.1](06-agent.md#61-one-conversation-on-the-pool) One conversation ·
[6.1.1](06-agent.md#611-the-arm-that-was-deleted) The arm that was deleted ·
[6.2](06-agent.md#62-the-blast-radius) The blast radius ·
[6.2.1](06-agent.md#621-the-step-budget) The step budget ·
[6.3](06-agent.md#63-what-failover-looks-like-in-practice) Failover in practice ·
[6.4](06-agent.md#64-why-it-is-shaped-this-way) Why it is shaped this way ·
[6.5](06-agent.md#65-what-makes-it-a-coding-agent) What makes it a coding agent

**[7. Observability](07-observability.md)** — what a run leaves behind, and why the answer is changing
&nbsp;&nbsp;&nbsp;&nbsp;[7.1](07-observability.md#71-why-two) Why two ·
[7.2](07-observability.md#72-langsmith) LangSmith ·
[7.3](07-observability.md#73-the-local-trace) The local trace ·
[7.4](07-observability.md#74-the-shape-is-a-contract) The shape is a contract ·
[7.5](07-observability.md#75-reading-routing-decisions-live) Reading routing decisions live ·
[7.6](07-observability.md#76-the-record-one-run-tree) The record: one run tree

**[15. The web explorer](15-explorer.md)** — the agent that reads the web and writes notes the coding agent can use
&nbsp;&nbsp;&nbsp;&nbsp;[15.1](15-explorer.md#151-what-it-is-for) What it is for ·
[15.2](15-explorer.md#152-the-web-on-a-free-tier) The web on a free tier ·
[15.2.1](15-explorer.md#1521-a-capability-is-a-fact-to-probe-not-to-infer) A capability is a fact to probe ·
[15.3](15-explorer.md#153-why-the-search-tool-fetches-the-page) Why the search tool fetches the page ·
[15.4](15-explorer.md#154-which-account-serves-a-search) Which account serves a search ·
[15.5](15-explorer.md#155-what-it-is-allowed-to-do) What it is allowed to do ·
[15.6](15-explorer.md#156-what-it-costs-a-run) What it costs a run ·
[15.7](15-explorer.md#157-measured-against-a-reference-research-agent) Measured against a reference research agent ·
[15.8](15-explorer.md#158-the-deep-research-port) The deep-research port ·
[15.9](15-explorer.md#159-what-the-grounded-gemini-search-was) What the grounded-Gemini search was

**[16. The agent protocol](16-agent-protocol.md)** — how one agent asks another for work, in the standard's vocabulary
&nbsp;&nbsp;&nbsp;&nbsp;[16.1](16-agent-protocol.md#161-the-problem-the-human-was-the-message-bus) The human was the message bus ·
[16.1.1](16-agent-protocol.md#1611-this-reopens-a-settled-decision-and-how-much-of-it) What it reopens ·
[16.2](16-agent-protocol.md#162-why-a2a-and-why-not-the-alternatives) Why A2A ·
[16.3](16-agent-protocol.md#163-why-the-transport-is-local) Why the transport is local ·
[16.4](16-agent-protocol.md#164-what-maps-onto-what) What maps onto what ·
[16.5](16-agent-protocol.md#165-one-delegation-end-to-end) One delegation ·
[16.6](16-agent-protocol.md#166-what-a-delegation-costs) What it costs ·
[16.7](16-agent-protocol.md#167-what-this-costs-and-what-is-unmeasured) What is unmeasured ·
[16.8](16-agent-protocol.md#168-what-is-deliberately-not-built) Not built ·
[16.9](16-agent-protocol.md#169-adding-a-third-agent) Adding a third agent ·
[16.10](16-agent-protocol.md#1610-what-the-first-live-run-showed) What the first live run showed

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
[9.8](09-scenarios.md#98-the-validation-gate) The validation gate ·
[9.9](09-scenarios.md#99-the-catalogue) The catalogue

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

### Part VI — Operations

**[14. Quota panel](14-quota-panel.md)** — what the pool has spent, and how close each account is to its wall
&nbsp;&nbsp;&nbsp;&nbsp;[14.1](14-quota-panel.md#141-the-question) The question ·
[14.2](14-quota-panel.md#142-the-path-of-one-number) The path of one number ·
[14.3](14-quota-panel.md#143-two-files-under-llm_routerusage) The two files ·
[14.4](14-quota-panel.md#144-one-source-and-what-it-misses) One source, and what it misses ·
[14.5](14-quota-panel.md#145-windows-and-when-they-reset) Windows and resets ·
[14.6](14-quota-panel.md#146-how-a-refused-attempt-is-counted) Refused attempts ·
[14.7](14-quota-panel.md#147-what-the-report-says) What the report says ·
[14.8](14-quota-panel.md#148-reading-it) Reading it ·
[14.9](14-quota-panel.md#149-what-it-deliberately-doesnt-do) What it doesn't do

**[17. Deployment](17-deployment.md)** — where this can run unattended, why most hosting platforms cannot run it, and what to change first
&nbsp;&nbsp;&nbsp;&nbsp;[17.1](17-deployment.md#171-the-question) The question ·
[17.2](17-deployment.md#172-what-the-workload-actually-is) What the workload is ·
[17.3](17-deployment.md#173-why-requestresponse-platforms-cannot-host-it) Why request/response platforms fail ·
[17.4](17-deployment.md#174-the-ceiling-is-the-pool-not-the-compute) The ceiling is the pool ·
[17.5](17-deployment.md#175-what-fits) What fits ·
[17.6](17-deployment.md#176-what-has-to-change-first) What has to change first ·
[17.7](17-deployment.md#177-provider-terms) Provider terms

**[18. Serving the agents](18-serving.md)** — the agents as HTTP endpoints, in a container, bound to a repository or a filesystem
&nbsp;&nbsp;&nbsp;&nbsp;[18.1](18-serving.md#181-what-this-adds-and-what-it-does-not) What it adds ·
[18.2](18-serving.md#182-the-surface) The surface ·
[18.3](18-serving.md#183-why-submission-does-not-block) Why submission doesn't block ·
[18.4](18-serving.md#184-binding-an-agent-to-a-repository-or-a-filesystem) Binding to a repo or filesystem ·
[18.5](18-serving.md#185-why-there-is-exactly-one-worker) Why one worker ·
[18.6](18-serving.md#186-the-token-is-not-optional) The token is not optional ·
[18.7](18-serving.md#187-what-has-to-be-a-volume) What has to be a volume ·
[18.8](18-serving.md#188-running-it) Running it ·
[18.9](18-serving.md#189-reaching-it-from-outside-the-house) Reaching it from outside

---

## Documents outside this wiki

| Where | What | Why it's not a wiki page |
| --- | --- | --- |
| [CLAUDE.md](../CLAUDE.md) | Agent entry point: north star, standards, index | Loaded into every agent's context; must stay short |
| [README.md](../README.md) | Human entry point: quick start, links out | Same reason |
| [evals/CONFIGS.md](../evals/CONFIGS.md) | Ledger of every configuration tried and its verdict | Append-only data, lives next to the results it indexes |
| `.claude/reports/` | One-off deep investigations, kept verbatim | Point-in-time research, not maintained state |

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
