[← Wiki index](README.md)

# Overview

*What this repo is trying to do, why it's shaped this way, and what it is not.*

## The goal

Run a coding agent continuously without paying per token.

Free-tier LLM APIs are individually useless for an unattended agent: each one
has a low, oddly-shaped rate limit (requests/minute, tokens/minute,
tokens/day), and the moment an agent trips one, the run stops. The bet of this
repo is that *several* free accounts across *several* providers, pooled behind
one interface, add up to enough continuous capacity to keep an agent working —
because the accounts rarely all exhaust at the same instant.

So the product is a **router**: given a request, pick a free account that is
available right now, and when it fails, quietly move to the next one. An agent
running unattended for hours should never stall because one free account hit
its limit.

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

The repo is three layers, each usable on its own, stacked:

| Layer | What it owns | Read |
| --- | --- | --- |
| **The pool** (`llm_router/`) | Which free account/model should serve the next request, and which ones are currently benched. Selection only — it never makes a call. | [The pool model](pool/model.md), [Failover](pool/failover.md), [Providers and limits](pool/providers.md) |
| **The agent** (`agent/`) | Two agents over one jail, whose single model is the pool: a coding agent that reads and edits a jailed working directory, runs its own tests and commits to its own branch, and a web explorer that researches and writes notes for it. | [The coding agent](agents/code.md), [Observability](evaluation/observability.md), [The web explorer](agents/explore.md) |
| **The evaluation** (`evals/`) | Deciding whether a change to either of the above actually helped, by running scenarios and comparing distributions — not by argument. | [Evaluation method](evaluation/method.md), [Scenarios](evaluation/scenarios.md), [Metrics](evaluation/metrics.md), [Status and roadmap](status.md) |

The layering is one-directional: `evals` drives `agent`, `agent` uses
`llm_router`, `llm_router` knows about neither. The one seam worth naming is
`RouterChatModel` — a LangChain `BaseChatModel` that wraps the pool so anything
in the LangChain ecosystem can consume it without knowing a router exists
([The failover loop](pool/failover.md#the-failover-loop)).

## What is deliberately not built

These are settled decisions, not gaps waiting to be filled. Re-opening one
needs a reason, not an opportunity.

- **Not a general-purpose LLM gateway.** No auth, no multi-tenancy, no billing,
  no admin UI. There is exactly one consumer: an agent loop, or the developer
  testing it.
- **Not chasing paid-tier quality.** Free tiers are the constraint the project
  exists to work within. "Just add a paid fallback" defeats the purpose.
- **No ToS circumvention.** Pooling means legitimately holding several free
  accounts. It does not mean evading a per-account rate limit through
  deception.
- **No dynamic scoring of providers.** Priority is a hand-assigned number.
  Latency/cost/success-rate scoring would add tuning surface with no evidence
  it's needed; cooldown already reacts to real-time availability
  ([Priority tiers](pool/model.md#priority-tiers)).

## Where the project actually stands

Honest summary, as of the last update to [Status and roadmap](status.md):

- The pool works and fails over in practice. A trivial one-line fix cost the
  conversational loop this replaced ~20 provider calls and ~227,000 input
  tokens — failover functions, but the pool gets walked hard, which is the whole
  reason the agent is shaped the way it is.
- Both agents run real tasks end to end, write files, and run their own tests.
- **There are five scenarios now**, three at L1/L2, but nothing has been run
  past n=2 and the original L0 is *exhausted as an instrument*: seven
  configurations were run against it and none was distinguishable from another.
- **There is one coding agent now**: the narrow-role arm was deleted rather
  than out-measured, so the cost gap it won on is an accepted risk rather than a
  closed question ([The arm that was deleted](agents/code.md#the-arm-that-was-deleted)). The
  blocking item is running the survivor against the scenarios that still
  discriminate ([What to do next](status.md#what-to-do-next-1)).
- Two Groq models are decommissioned and return `404 model_not_found`. The
  router retires the member and the run carries on, but only for that process,
  so every fresh run spends one attempt rediscovering each dead model
  ([Known gaps](pool/failover.md#known-gaps)).
- The scenario repo is **not backed up**: four of five scenario tags exist only
  on the machine that authored them
  ([Blockers](status.md#blockers)).

**Work in progress lives on a branch, not on `master`.** Check `git branch` before
assuming what is present.

## Reading paths

| If you want to… | Read |
| --- | --- |
| understand the whole thing quickly | this page, then [Repo map](#repo-map), then [Failover](pool/failover.md) |
| add a free account or provider | [Providers and limits](pool/providers.md) |
| run the coding agent on a project | [The coding agent](agents/code.md) |
| change the router/prompt/loop and prove it helped | [Evaluation method](evaluation/method.md) → [Driving the free agents](operations/driving-agents.md) |
| decide what gets built next | [Roadmap and scope](status.md#roadmap-and-scope) |

---



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

### `agent/` — the coding agent

`agent/utils/` is what every agent needs and none of them owns. Nothing in it knows
what a session is.

| File | The question it answers |
| --- | --- |
| [chat_model.py](../agent/utils/chat_model.py) | *What actually happens on a call, including retry?* — the failover loop, as a LangChain `BaseChatModel` |
| [trace.py](../agent/utils/trace.py) | *What happened during a run, durably?* — the `EVAL_TRACE_FILE` JSONL callback handler |
| [run_tree.py](../agent/utils/run_tree.py) | *What happened during a run, readably?* — the LangSmith run tree, fetched after the run and written down condensed ([The record: one run tree](evaluation/observability.md#the-record-one-run-tree)) |
| [pool.py](../agent/utils/pool.py) | *Which model does an agent run on?* — the pool as one `RouterChatModel` that routes only to members holding the context floor, and refuses before the run when none does |
| [prompts.py](../agent/utils/prompts.py) + [prompts/](../agent/utils/prompts/) | *What is every agent told about where it runs?* — headless, the pool's identity, where `/` is; and `fill`, the one way any prompt file is filled |

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
| [prompts/](../agent/explore/prompts/) | *What is it told?* — `system.md` (its job, what it cannot do, when it is finished), then LangChain's deep-research method, ported close to verbatim with every deviation marked `ADAPTED` ([The deep-research port](agents/explore.md#the-deep-research-port)) |
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

### `evals/` — the measurement harness

Code lives with the code it measures; scenario *data* does not (see
[Related repos](#related-repos) below).

| Path | Role |
| --- | --- |
| [`__main__.py`](../evals/__main__.py) | CLI: `validate`, `run`, `show` |
| [`scenario.py`](../evals/scenario.py) | Load and materialize a scenario from the scenario repo |
| [`agent_config.py`](../evals/agent_config.py) | Resolve a configuration to a pinned SHA + overrides, and check it out for the batch |
| [`run.py`](../evals/run.py) | Execute one run: materialize → run → capture |
| [`verify.py`](../evals/verify.py) | The `fail_to_pass`/`pass_to_pass` gate and scenario validation |
| [`metrics.py`](../evals/metrics.py) | Derive every automatic metric from `trace.jsonl` and the diff |
| [`fake_agent.py`](../evals/fake_agent.py) | Stub agent, so the runner's own paths can be exercised without spending quota |
| [`configs/*.yaml`](../evals/configs/) | One file per agent configuration under test |
| [`CONFIGS.md`](../evals/CONFIGS.md) | The ledger: every configuration tried, its verdict, and why |
| `results/runs/<run_id>/` | One self-contained directory per run — durable evidence, on disk only. Gitignored: an artifact, never committed. Cite a run by its id |

### Everything else

| Path | Role |
| --- | --- |
| [CLAUDE.md](../CLAUDE.md) | Agent entry point: north star, standards, index. Kept short on purpose. |
| [README.md](../README.md) | Human entry point: quick start and links out. Also short on purpose. |
| `docs/` | This wiki. The place to understand the system without reading source. |
| [.claude/settings.json](../.claude/settings.json) | The `Stop` hook that keeps this wiki from drifting ([Maintenance](README.md#maintenance)) |
| `.claude/reports/` | Deep one-off investigations, kept verbatim. |
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
- LangSmith runs — useful for watching, but they expire, which is precisely why
  the local trace exists ([Observability](evaluation/observability.md)).
