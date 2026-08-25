[← Wiki index](README.md)

# 2. Repo map

*What lives where, what question each file answers, and which sibling repos
this one depends on.*

## 2.1 The rule

Every file has one job, and the split follows the questions you'd actually ask.
If you can't name the question a file answers, it probably shouldn't be a
separate file.

## 2.2 `llm_router/` — the pool

Selection and state. Never makes an API call itself.

| File | The question it answers |
| --- | --- |
| [base_provider.py](../llm_router/base_provider.py) | *Is this account usable right now?* — the `LLMProvider` ABC, cooldown state, `is_transient()` error classification, `estimate_tokens()` |
| [providers.py](../llm_router/providers.py) | *Which SDK do I call for this provider type?* — `OpenAICompatibleProvider` (Groq and anything OpenAI-shaped) and `GeminiProvider` |
| [loader.py](../llm_router/loader.py) | *How do accounts get built from config?* — reads YAML + `.env`, fans models across accounts, skips entries with missing keys |
| [router.py](../llm_router/router.py) | *Which account is best right now?* — `AutonomousLLMRouter.get_best_provider()` |
| [__main__.py](../llm_router/__main__.py) | *What are my limits, before I've run anything?* — `python -m llm_router` builds the pool and writes its snapshot, calling nothing |
| [usage.py](../llm_router/usage.py) | *What has this pool spent?* — the append-only ledger and the pool snapshot behind [14. Quota panel](14-quota-panel.md) |
| [quota/](../llm_router/quota/) | *How close is each account to its wall?* — the reader over that ledger: table, JSON, filterable HTML panel |
| [config.yaml](../llm_router/config.yaml) | *What's in the pool?* — accounts, models and their declared `limits`, see [5.2](05-providers.md#52-config-schema) |
| `.env` (gitignored) | The actual API keys. Never in code or config. |

## 2.3 `agent/` — the coding agent

`agent/runtime/` is the substrate anything agentic runs on. Nothing in it knows
what a session is.

| File | The question it answers |
| --- | --- |
| [chat_model.py](../agent/runtime/chat_model.py) | *What actually happens on a call, including retry?* — the failover loop, as a LangChain `BaseChatModel` |
| [backend.py](../agent/runtime/backend.py) | *What is the agent allowed to execute?* — a filesystem jail plus an `execute` allowlist of `python`/`pytest`/`git` |
| [tools.py](../agent/runtime/tools.py) | *What can a node actually do?* — narrow tools over `RestrictedShellBackend`, one small schema each |
| [trace.py](../agent/runtime/trace.py) | *What happened during a run, durably?* — the `EVAL_TRACE_FILE` JSONL callback handler |

`agent/harness/` is the agent itself: a LangGraph state machine of narrow nodes
over one shared log. See [6.4](06-agent.md#64-the-graph).

| File | The question it answers |
| --- | --- |
| [nodes/](../agent/harness/nodes/) | *Who does what, with which tools, and how is it judged?* — one file per node, each declaring its prompt, its tools, the log entries it reads, and how its result is read |
| [nodes/base.py](../agent/harness/nodes/base.py) | *What is a node, and what does one call look like?* — the `Node` fields, then build the prompt, a few tool rounds, throw the conversation away |
| [graph.py](../agent/harness/graph.py) | *What happens when, and what is the model not allowed to decide?* — the edges between nodes and every deterministic veto |
| [log.py](../agent/harness/log.py) | *What does a node get to see?* — everything the session knows, as one ordered log, and the per-kind caps that **are** the context budget |
| [protocol.py](../agent/harness/protocol.py) | *How does context get from the orchestrator to a node?* — the brief down, the report back |
| [record/](../agent/harness/record/) | *What does a session leave behind?* — git, the journal, the per-turn transcript, the rationale, the notes file |
| [session.py](../agent/harness/session.py) | *What is one run, start to finish?* — wiring, the step budget, resume, and the account it writes |
| [`__main__.py`](../agent/harness/__main__.py) | CLI: workdir as an argument, task on stdin |

## 2.4 `evals/` — the measurement harness

Code lives with the code it measures; scenario *data* does not (see
[2.6](#26-related-repos) below).

| Path | Role |
| --- | --- |
| [`__main__.py`](../evals/__main__.py) | CLI: `validate`, `run`, `show` |
| [`scenario.py`](../evals/scenario.py) | Load and materialize a scenario from the scenario repo |
| [`agent_config.py`](../evals/agent_config.py) | Resolve a configuration to a git worktree + overrides |
| [`run.py`](../evals/run.py) | Execute one run: materialize → run → capture |
| [`verify.py`](../evals/verify.py) | The `fail_to_pass`/`pass_to_pass` gate and scenario validation |
| [`metrics.py`](../evals/metrics.py) | Derive every automatic metric from `trace.jsonl` and the diff |
| [`fake_agent.py`](../evals/fake_agent.py) | Stub agent, so the runner's own paths can be exercised without spending quota |
| [`configs/*.yaml`](../evals/configs/) | One file per agent configuration under test |
| [`CONFIGS.md`](../evals/CONFIGS.md) | The ledger: every configuration tried, its verdict, and why |
| `results/runs/<run_id>/` | One self-contained directory per run — durable evidence, on disk only. Gitignored: an artifact, never committed. Cite a run by its id |
| `.worktrees/` | Scratch checkouts of configurations under test (gitignored) |

## 2.5 Everything else

| Path | Role |
| --- | --- |
| [CLAUDE.md](../CLAUDE.md) | Agent entry point: north star, standards, index. Kept short on purpose. |
| [README.md](../README.md) | Human entry point: quick start and links out. Also short on purpose. |
| `docs/` | This wiki. The place to understand the system without reading source. |
| [.claude/settings.json](../.claude/settings.json) | The `Stop` hook that keeps this wiki from drifting ([12.4](12-development-harness.md#124-how-the-docs-stay-current)) |
| [.claude/reports/](../.claude/reports/) | Deep one-off investigations, kept verbatim. |
| `tests/` | `llm_router/` and `agent/` unit tests, plus `smoke_test.py` — a single real prompt through the pool to check keys and config are wired |

## 2.6 Related repos

Three sibling directories matter, and none of them is a submodule — they are
independent repos that happen to live next to each other.

| Repo | Relationship |
| --- | --- |
| **`agent_evals`** | The scenario library: frozen codebase states with something wrong in them. Data only — no harness, no results. The eval runner defaults to finding it as a sibling of this repo; override with `--scenarios` or `EVAL_SCENARIOS`. See [9. Scenarios](09-scenarios.md). |
| **`closet_ai`** | A wardrobe-inventory app being built *by* this repo's agents. It is the real workload that stress-tests the harness — every failure mode found there is input to this project's roadmap. |
| this repo | The agent under test, plus the harness that tests it. |

The split of `agent_evals` from this repo is deliberate and load-bearing:
agent configurations are *branches of this repo*, so anything shared and
append-only (a results index, a scenario file) would conflict on every merge.
Scenarios live outside; results are one file per run with no index
([8.3](08-evaluation-method.md#83-where-things-live)).

## 2.7 State that lives outside git

Worth knowing before debugging something that "should work":

- `llm_router/.env` — API keys, and the LangSmith/trace env vars. Gitignored.
  Without it the pool loads zero providers and the agent exits immediately.
- Cooldown state is **in-memory, per process**. Two agent processes do not
  share knowledge of which accounts are hot. This is a known limitation, not a
  bug ([13.4](13-roadmap.md#134-open-questions)).
- `llm_router/.usage/` — this machine's usage ledger and pool snapshot.
  Gitignored, rebuilt as the router runs; deleting it only loses history
  ([14.3](14-quota-panel.md#143-two-files-under-llm_routerusage)).
- `evals/.worktrees/` — throwaway checkouts, safe to delete.
- LangSmith runs — useful for watching, but they expire, which is precisely why
  the local trace exists ([7. Observability](07-observability.md)).

---

**Previous:** [← 1. Overview](01-overview.md) · **Next:** [3. The pool model →](03-pool-model.md)
