[← Wiki index](README.md)

# Overview

*What this repo is trying to do, why it is shaped this way, what it is not, and
what lives where.*

## The goal

**A standing maintainer:** an agent that works a project unattended for hours
on its own branch, keeps its documentation current, and leaves an account a
human reviews instead of reading the diff
([design note](design/long-run-harness.md)).

What makes that affordable is the **pool**. Free-tier LLM APIs are individually
useless for an unattended agent: each has a low, oddly-shaped rate limit
(requests/minute, tokens/minute, tokens/day), and the moment an agent trips one,
the run stops. The bet of this repo is that *several* free accounts across
*several* providers, pooled behind one router, add up to enough continuous
capacity — because the accounts rarely all exhaust at the same instant. The
router picks an account that is available right now and, when it fails, quietly
moves to the next. A run should never stall because one account hit its limit.

The router is the substrate, not the product: it is largely settled, and work
on it now serves the hours the maintainer has to stay up.

## Why this is infrastructure, not a demo

The interesting engineering is not "call an LLM". It is everything around the
call:

- Deciding whether an error means *"this account is busy, try another"* or
  *"your code is broken, stop"* — and never confusing the two ([Classifying a failure](pool/failover.md#classifying-a-failure)).
- Benching a failing account for the right amount of time, and letting it back
  in ([Cooldown and backoff](pool/failover.md#cooldown-and-backoff)).
- Sending a large request to a model that can physically hold it, instead of
  discovering that by getting rejected eleven times ([Size-aware selection](pool/failover.md#size-aware-selection)).
- Making the whole thing observable enough that you can tell *which* model
  actually did the work ([Observability](evaluation/observability.md)).

Every one of those is a correctness problem with a boring right answer, which
is why the coding standard for this repo is "obvious control flow over
cleverness". The code has to survive hours of unattended running.

## The three subsystems

| Layer | What it owns | Read |
| --- | --- | --- |
| **The pool** (`llm_router/`) | Which account × model serves the next request, which are benched, and how much free tier is left. Selection only — it never makes a call. | [pool/](pool/model.md) |
| **The agents** (`agent/`) | Three `deepagents` agents whose single model is the pool: the coding agent, the web explorer, and the improvement agent that reads the other two's runs. Any of them can run another as a command, and all three can be served over HTTP. | [agents/](agents/code.md), [Serving](operations/serving.md) |
| **The evaluation** (`evals/`) | Whether a change to either of the above helped — scenarios with hidden tests for the outcome, probes for single decisions — decided by measurement, not argument. | [evaluation/](evaluation/method.md) |

The layering is one-directional: `evals` drives `agent`, `agent` uses
`llm_router`, `llm_router` knows about neither. The one seam worth naming is
`RouterChatModel` — a LangChain `BaseChatModel` that wraps the pool so anything
in the LangChain ecosystem can consume it without knowing a router exists
([The failover loop](pool/failover.md#the-failover-loop)).

**The agents are not bespoke.** `deepagents` ships the tools, the planner,
sub-agents, compaction, skills and the backends; an agent here is a
configuration of it, and its behaviour lives in Markdown.

## What is deliberately not built

Re-opening one of these needs a reason, not an opportunity.

- **Not a general-purpose LLM gateway.** No auth, no multi-tenancy, no billing,
  no admin UI. There is exactly one consumer: an agent loop, or the developer
  testing it.
- **Not chasing paid-tier quality.** Free tiers are the constraint the project
  exists to work within. "Just add a paid fallback" defeats the purpose.
- **No ToS circumvention.** Pooling means legitimately holding several free
  accounts. It does not mean evading a per-account rate limit through
  deception.

## Settled decisions

Don't re-litigate these without new evidence. Each was chosen for a reason that
still holds.

| Decision | Why |
| --- | --- |
| Priority is a hand-assigned number; no dynamic scoring. | Cooldown already reacts to real-time availability. Scoring adds tuning surface with no evidence it's needed ([Priority tiers](pool/model.md#priority-tiers)). |
| The router selects; it never generates. | Giving it a `.generate()` would force `llm_router` to import `agent` and invert the layering. |
| `RouterChatModel` subclasses `BaseChatModel` rather than being a bespoke client. | Provider SDKs already handle message formatting, tool parsing and streaming; and the pool becomes usable anywhere in the LangChain ecosystem. |
| Non-transient errors are raised, never rerouted. | Silently rerouting a malformed-request bug burns the pool and hides the bug behind "all providers exhausted". |
| Provider SDKs run with `max_retries=0`. | The SDK would retry the same dead account — the job the router owns one level up. |
| Only the sync path is implemented. | Every caller is sync. A hand-written async loop was built, found unused, and removed. |
| One coding agent, configured like `deepagents-code`; the narrow-role harness is deleted. | A decision about what to maintain, not a measurement — the deleted arm was cheaper ([The arm that was deleted](agents/code.md#the-arm-that-was-deleted)). |
| Agents ask each other for work by running the command; no protocol. | A human was the message bus, which does not survive an unattended run. A command line has nothing to keep in step; the deliverable is still a file ([Delegation](agents/delegation.md)). |
| The record of a run is the LangSmith run tree, *snapshotted* to disk, plus the local JSONL. | A verdict must rest on files on disk. What expiry forbids is depending on the hosted copy at scoring time, not fetching it once while it exists ([The record: one run tree](evaluation/observability.md#the-record-one-run-tree)). |
| Scenarios live in a separate repo from the harness. | Configurations are branches of this repo; anything shared and append-only would conflict on every merge. |
| Results are one directory per run, with no index. | Same reason. A summary is a glob. |

## Reading paths

| If you want to… | Read |
| --- | --- |
| understand the whole thing quickly | this page, then [Status and roadmap](status.md), then [Failover](pool/failover.md) |
| add a free account or provider | [Providers and limits](pool/providers.md) |
| run an agent on a project | [The coding agent](agents/code.md), [Driving the free agents](operations/driving-agents.md) |
| change how an agent behaves and prove it helped | [Changing how an agent behaves](evaluation/changing-behaviour.md), [Evaluation method](evaluation/method.md) |
| run it somewhere other than a laptop | [Serving the agents](operations/serving.md), [Deployment](operations/deployment.md) |
| decide what gets built next | [Status and roadmap](status.md) |

## Repo map

*What lives where, what question each file answers, and which sibling repos
this one depends on.*

### The rule

Every file has one job, and the split follows the questions you'd actually ask.
If you can't name the question a file answers, it probably shouldn't be a
separate file.

### `llm_router/` — the pool

Selection and state. Never makes an API call itself.

| File | The question it answers |
| --- | --- |
| [base_provider.py](../llm_router/base_provider.py) | *Is this account usable right now?* — the `LLMProvider` ABC, cooldown state, `is_transient()` error classification, `estimate_tokens()` |
| [providers.py](../llm_router/providers.py) | *Which SDK do I call for this provider type?* — `OpenAICompatibleProvider` (Groq and anything OpenAI-shaped) and `GeminiProvider` |
| [loader.py](../llm_router/loader.py) | *How do accounts get built from config?* — reads YAML + `.env`, fans models across accounts, skips entries with missing keys |
| [router.py](../llm_router/router.py) | *Which account is best right now?* — `AutonomousLLMRouter.get_best_provider()` |
| [__main__.py](../llm_router/__main__.py) | *What are my limits, before I've run anything?* — `python -m llm_router` builds the pool and writes its snapshot, calling nothing |
| [usage.py](../llm_router/usage.py) | *What has this pool spent?* — the append-only ledger and the pool snapshot behind [Quota panel](pool/quota.md) |
| [quota/](../llm_router/quota/) | *How close is each account to its wall?* — the reader over that ledger: table, JSON, filterable HTML panel. [`budget.py`](../llm_router/quota/budget.py) answers the router's one question off the same data: who has spent their requests-per-day ([Skipping a member whose day is spent](pool/failover.md#skipping-a-member-whose-day-is-spent)) |
| [config.yaml](../llm_router/config.yaml) | *What's in the pool?* — accounts, models and their declared `limits`, see [Config schema](pool/providers.md#config-schema) |
| `.env` (gitignored) | The actual API keys. Never in code or config. |

### `agent/` — the agents

`agent/utils/` is what every agent needs and none of them owns. Nothing in it knows
what a session is.

| File | The question it answers |
| --- | --- |
| [chat_model.py](../agent/utils/chat_model.py) | *What actually happens on a call, including retry?* — the failover loop, as a LangChain `BaseChatModel` |
| [trace.py](../agent/utils/trace.py) | *What happened during a run, durably?* — the `EVAL_TRACE_FILE` JSONL callback handler |
| [run_tree.py](../agent/utils/run_tree.py) | *What happened during a run, readably?* — the LangSmith run tree, fetched after the run and written down condensed ([The record: one run tree](evaluation/observability.md#the-record-one-run-tree)) |
| [pool.py](../agent/utils/pool.py) | *Which model does an agent run on?* — the pool as one `RouterChatModel` that routes only to members holding the context floor, and refuses before the run when none does |
| [prompts.py](../agent/utils/prompts.py) + [prompts/](../agent/utils/prompts/) | *What is every agent told about where it runs?* — headless, the pool's identity, where `/` is; and `fill`, the one way any prompt file is filled |
| [surface.py](../agent/utils/surface.py) | *What does the framework inject that a harness profile cannot reach?* — the prose and tools each agent removes or rewrites |
| [file_tools.py](../agent/utils/file_tools.py) | *How much of a file does `read_file` return?* — the whole file, by default ([`read_file` reads the whole file](agents/code.md#read_file-reads-the-whole-file)) |
| [awake.py](../agent/utils/awake.py) | *Will the machine sleep mid-run?* — holds off idle sleep for the length of a run |

Web search is not here either: the account pool and the failover between
accounts are [llm_router/tavily_router.py](../llm_router/tavily_router.py), and
the tool over it is [agent/explore/tools.py](../agent/explore/tools.py) — the
one agent that searches owns it.

There is no backend module. The backends are deepagents' own, built at the one
place each agent is assembled: `LocalShellBackend` where an agent runs commands,
`FilesystemBackend` where it only reads and writes
([The blast radius](agents/code.md#the-blast-radius)).

`agent/code/` is the **coding agent**: `create_deep_agent` over that backend,
configured the way `deepagents-code` configures one. See
[What makes it a coding agent](agents/code.md#what-makes-it-a-coding-agent). It is mostly configuration —
the loop, the tools and the compaction come from the SDK — so, like the
explorer, it is two Python files and a directory of Markdown.

| File | The question it answers |
| --- | --- |
| [agent.py](../agent/code/agent.py) | *How is it built?* — the whole harness, top to bottom: the step budget and the programs `execute` runs, the templating that fills the prompt, the project section it starts with, `create_deep_agent` over the jailed shell, and one run with its wrap-up |
| [prompts/](../agent/code/prompts/) | *What is it told?* — `system.md`, the ported prompt; the two sections a run can switch off (`contradicted_requests.md`, `project_notes.md`); `wrap_up.md`, what a run that spends its budget is told; and the two sections the framework would otherwise write for us (`skills.md`, `memory.md`) |
| [`__main__.py`](../agent/code/__main__.py) | CLI: workdir as an argument, task on stdin; prints the final message, then what git says moved |

`agent/explore/` is the **web explorer**: the same loop and the same jail, with
its tools pointed outward and no shell at all. Two Python files and two
directories of Markdown, and the Markdown is the behaviour. See
[The web explorer](agents/explore.md).

| File | The question it answers |
| --- | --- |
| [agent.py](../agent/explore/agent.py) | *How is it built?* — the whole harness, top to bottom: the budgets and the tools taken away, the templating that fills every Markdown file, the model and search pools, the three tools it adds, the middleware that fits the framework's tools and prompt to it, the two sub-agents, and one run |
| [prompts/](../agent/explore/prompts/) | *What is it told?* — `system.md` (its job, what it cannot do, when it is finished), then LangChain's deep-research method, ported close to verbatim ([The deep-research port](agents/explore.md#the-deep-research-port)) |
| [tools.py](../agent/explore/tools.py) | *What can it do that the framework does not ship?* — `tavily_search` over the search pool, `think_tool`, and what its research directory holds |
| [tool_descriptions/](../agent/explore/tool_descriptions/) | *What is each tool for?* — one Markdown file per tool, and that file is the description the model reads ([What it is allowed to do](agents/explore.md#what-it-is-allowed-to-do)) |
| [`__main__.py`](../agent/explore/__main__.py) | CLI, and how another agent reaches it: same shape as the coding agent, final message first, then the notes *this* run wrote ([Delegation](agents/delegation.md)) |

`agent/improve/` is the **improvement agent**: the same loop and the same jail,
pointed at what the other two *recorded* rather than at a project. It is the one
agent here that cannot write a file. It is built like the other two — `agent.py`,
`__main__.py`, and Markdown the model reads — plus what its tools do and read. See
[The improvement agent](agents/improve.md).

| File | The question it answers |
| --- | --- |
| [records.py](../agent/improve/records.py) | *What evidence is there?* — every recorded run under `evals/results/runs/` and any live root, whether it carries a hidden-test verdict, and whether a stored signature matches it |
| [issues.py](../agent/improve/issues.py) | *What is already known?* — the ledger: a named failure, its signature, what was delegated, and the check that closed or reopened it |
| [scenarios.py](../agent/improve/scenarios.py) | *What eval case would have caught this?* — turning a run that went wrong into a draft scenario |
| [repo.py](../agent/improve/repo.py) | *Where does a fix land, and how is it undone?* — a branch per fix in the repository it is running on |
| [tools.py](../agent/improve/tools.py) | *What can it do?* — seven tools, one per stage of the loop, and what each returns; every section bounded, because a `trace.json` is megabytes |
| [agent.py](../agent/improve/agent.py) | *How is it built?* — the whole harness, top to bottom: `git` and nothing else executable, the open ledger in the prompt, the filesystem writes refused as a readable message so the diff is always someone else's, the agent, and one pass |
| [prompts/](../agent/improve/prompts/) | *What is it told?* — `system.md`, the standing pass it runs with no task, and the refusal a write gets |
| [tool_descriptions/](../agent/improve/tool_descriptions/) | *What is each tool for?* — one Markdown file per tool, and that file is the description the model reads |
| [`__main__.py`](../agent/improve/__main__.py) | CLI: same shape again, and with no task it runs the standing pass over the ledger |

**How one agent asks another for work: it runs it.** Every agent is a command,
and the coding agent already has `execute`. See [Delegation](agents/delegation.md).

| File | The question it answers |
| --- | --- |
| [agent/delegation.py](../agent/delegation.py) | *Which agents may this one run, and how is it told?* — the one module that knows about all of them: the prompt paragraph naming each command, the probe that offers `explore` only if the pool can really search, and the `subprocess.run` `delegate_fix` uses |
| [agent/utils/cli.py](../agent/utils/cli.py) | *How does a command take its task?* — workdir plus `--task` or stdin, shared by all three, because `execute` has no stdin to pipe a brief into |
| [agent/utils/gitstate.py](../agent/utils/gitstate.py) | *Did the delegate actually do anything?* — what git says moved, rendered verdict-first, above whatever the session said about itself |

`agent/serve/` puts the three agents behind HTTP for a container: a caller
submits a task and polls, and the server runs the same command line a person
would. See [Serving the agents](operations/serving.md).

| File | The question it answers |
| --- | --- |
| [app.py](../agent/serve/app.py) | *What can a caller ask for?* — the endpoints, and the bearer token that guards them |
| [runner.py](../agent/serve/runner.py) | *Who runs the task?* — exactly one worker, because cooldown and the usage ledger are per process |
| [task.py](../agent/serve/task.py) | *What is a task, and what does it report?* — its state, and what git says moved |
| [workspace.py](../agent/serve/workspace.py) | *What is an agent bound to?* — a workspace name under one mounted root, mounted in or cloned |

### `evals/` — the measurement harness

Code lives with the code it measures; scenario *data* does not (see
[Related repos](#related-repos) below).

| Path | Role |
| --- | --- |
| [`__main__.py`](../evals/__main__.py) | CLI: `validate`, `run`, `show`, `index`, `mine`, `bundle`, `probes` |
| [`scenario.py`](../evals/scenario.py) | Load and materialize a scenario from the scenario repo |
| [`agent_config.py`](../evals/agent_config.py) | Resolve a configuration to a pinned SHA + overrides, and check it out for the batch |
| [`run.py`](../evals/run.py) | Execute one run: materialize → run → capture |
| [`verify.py`](../evals/verify.py) | The `fail_to_pass`/`pass_to_pass` gate and scenario validation |
| [`metrics.py`](../evals/metrics.py) | Derive every automatic metric from `trace.jsonl` and the diff |
| [`splits.py`](../evals/splits.py) + [`splits.yaml`](../evals/splits.yaml) | The train/holdout split, by topic, and the gate computed over it |
| [`probes.py`](../evals/probes.py) + [`probes/`](../evals/probes/) | The small tests: one situation, one decision; [`probe_dataset.py`](../evals/probe_dataset.py) pushes them to LangSmith ([Probes](evaluation/probes.md)) |
| [`catalog.py`](../evals/catalog.py) | `index`: the scenario repo's generated catalogue and results pages |
| [`mine.py`](../evals/mine.py) | Recorded sessions as raw material for new scenarios ([ARCHETYPES.md](../evals/ARCHETYPES.md)) |
| [`bundle.py`](../evals/bundle.py) | A batch's evidence in one file, for J2 error analysis |
| [`research_trajectory.py`](../evals/research_trajectory.py) | Checks the explorer's numeric rules against a run |
| [`fake_agent.py`](../evals/fake_agent.py) | Stub agent, so the runner's own paths can be exercised without spending quota |
| [`configs/*.yaml`](../evals/configs/) | One file per agent configuration under test |
| [`CONFIGS.md`](../evals/CONFIGS.md) | The ledger: every configuration tried, its verdict, and why |
| `results/runs/<run_id>/` | One self-contained directory per run — durable evidence, on disk only. Gitignored: an artifact, never committed. Cite a run by its id |

### Everything else

| Path | Role |
| --- | --- |
| [CLAUDE.md](../CLAUDE.md), [AGENTS.md](../AGENTS.md) | Agent entry point for Claude Code and Codex: north star, standards, layout. Kept short on purpose. |
| [README.md](../README.md) | Human entry point: quick start and links out. Also short on purpose. |
| `docs/` | This wiki. The place to understand the system without reading source. |
| [.claude/settings.json](../.claude/settings.json) | The `Stop` hook that keeps this wiki from drifting ([Maintenance](README.md#maintenance)) |
| [.claude/skills/](../.claude/skills/), [.claude/agents/](../.claude/agents/) | The skills (`deepagents`, `behaviour-change`, `j2-error-analysis`) and the `trace-reviewer` subagent used to work on this repo |
| `tests/` | `llm_router/` and `agent/` unit tests, plus `smoke_test.py` — a single real prompt through the pool to check keys and config are wired |

### Related repos

Three sibling directories matter, and none of them is a submodule — they are
independent repos that happen to live next to each other.

| Repo | Relationship |
| --- | --- |
| **`agent_evals`** | The scenario library: frozen codebase states with something wrong in them. Data only — no harness, no results. The eval runner defaults to finding it as a sibling of this repo; override with `--scenarios` or `EVAL_SCENARIOS`. See [Scenarios](evaluation/scenarios.md). |
| **`closet_ai`** | A wardrobe-inventory app being built *by* this repo's agents. It is the real workload that stress-tests the harness — every failure mode found there is input to this project's roadmap. |
| this repo | The agent under test, plus the harness that tests it. |

The split of `agent_evals` from this repo is deliberate and load-bearing:
agent configurations are *branches of this repo*, so anything shared and
append-only (a results index, a scenario file) would conflict on every merge.
Scenarios live outside; results are one file per run with no index
([Where things live](evaluation/method.md#where-things-live)).

### State that lives outside git

Worth knowing before debugging something that "should work":

- `llm_router/.env` — API keys, and the LangSmith/trace env vars. Gitignored.
  Without it the pool loads zero providers and the agent exits immediately.
- Cooldown state is **in-memory, per process**. Two agent processes do not
  share knowledge of which accounts are hot. This is a known limitation, not a
  bug ([Open questions](status.md#open-questions)).
- `llm_router/.usage/` — this machine's usage ledger and pool snapshot.
  Gitignored, rebuilt as the router runs; deleting it only loses history
  ([Two files, under `llm_router/.usage/`](pool/quota.md#two-files-under-llm_routerusage)).
- LangSmith runs — they expire, which is why a run's tree is snapshotted to
  disk when the run ends, and why probes keep their claims in git and only
  their runs in LangSmith ([Observability](evaluation/observability.md)).
