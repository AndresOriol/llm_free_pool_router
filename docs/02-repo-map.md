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
| [quota/](../llm_router/quota/) | *How close is each account to its wall?* — the reader over that ledger: table, JSON, filterable HTML panel. [`budget.py`](../llm_router/quota/budget.py) answers the router's one question off the same data: who has spent their requests-per-day ([4.2.1](04-failover.md#421-skipping-a-member-whose-day-is-spent)) |
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

`agent/code/` is the **coding agent**: `create_deep_agent` over that backend,
configured the way `deepagents-code` configures one. See
[6.5](06-agent.md#65-what-makes-it-a-coding-agent). It is mostly configuration —
the loop, the tools and the compaction come from the SDK.

| File | The question it answers |
| --- | --- |
| [session.py](../agent/code/session.py) | *What turns a generic deep agent into this coding agent?* — backend, middleware, prompt, subagent, and the context floor check that fails before the run rather than during it |
| [prompt.py](../agent/code/prompt.py) + [system_prompt.md](../agent/code/system_prompt.md) | *What is the agent told?* — the ported prompt, and the three sections only the running configuration can fill |
| [context.py](../agent/code/context.py) | *What does it know before its first tool call?* — git branch, status and a depth-limited tree, so orientation isn't bought with model calls |
| [shell.py](../agent/code/shell.py) | *How is a refused command explained?* — the allowlist as a readable tool message, not an exception |
| [trace.py](../agent/code/trace.py) | *What happened during a run, durably?* — the LangSmith run tree, fetched and written down ([7.6](07-observability.md#76-the-record-one-run-tree)) |
| [`__main__.py`](../agent/code/__main__.py) | CLI: workdir as an argument, task on stdin |

`agent/explore/` is the **web explorer**: the same loop and the same jail, with
its tools pointed outward and no shell at all. See
[15. The web explorer](15-explorer.md).

| File | The question it answers |
| --- | --- |
| [research_tools.py](../agent/explore/research_tools.py) | *How does an agent reach the web?* — `tavily_search` (Tavily finds URLs, httpx fetches, markdownify converts, so the **page** reaches the model), `think_tool`, and `research_status`, which lists what the research has written |
| [tools.py](../agent/explore/tools.py) + [descriptions/](../agent/explore/descriptions/) | *Which tools does it have, and what is it told about them?* — three lists (dropped tools, rewritten descriptions, removed framework prose) and one Markdown file per tool ([15.5](15-explorer.md#155-what-it-is-allowed-to-do)) |
| [deep_prompts.py](../agent/explore/deep_prompts.py) + [prompts/](../agent/explore/prompts/) | *Whose method is this?* — LangChain's deep-research prompts, ported close to verbatim into three Markdown files, with every deviation marked `ADAPTED`; the module is the provenance ([15.8](15-explorer.md#158-the-deep-research-port)) |
| [session.py](../agent/explore/session.py) | *How does it differ from the coding agent?* — an orchestrator over a `research-agent` sub-agent, the web tools in, every program out, and the research directory — named per run — created before the first write |
| [prompt.py](../agent/explore/prompt.py) + [system_prompt.md](../agent/explore/system_prompt.md) | *What does it know that upstream cannot?* — which pool serves a call, where the jail's `/` is, that nobody is watching. The method comes from `deep_prompts.py` |
| [`__main__.py`](../agent/explore/__main__.py) | CLI, and how another agent reaches it: same shape as the coding agent, final message first, then the notes *this* run wrote ([16](16-delegation.md)) |

`agent/improve/` is the **improvement agent**: the same loop and the same jail,
pointed at what the other two *recorded* rather than at a project. It is the one
agent here that cannot write a file. See
[19. The improvement agent](19-improvement-agent.md).

| File | The question it answers |
| --- | --- |
| [records.py](../agent/improve/records.py) | *What evidence is there?* — every recorded run under `evals/results/runs/` and any live root, whether it carries a hidden-test verdict, and whether a stored signature matches it |
| [issues.py](../agent/improve/issues.py) | *What is already known?* — the ledger: a named failure, its signature, what was delegated, and the check that closed or reopened it |
| [tools.py](../agent/improve/tools.py) | *What can it do?* — six tools, one per stage of the loop; every section bounded, because a `trace.json` is megabytes |
| [readonly.py](../agent/improve/readonly.py) | *Why can't it just fix it?* — the filesystem writes, refused as a readable message, so the diff is always someone else's |
| [session.py](../agent/improve/session.py) | *How does it differ from the coding agent?* — write tools refused, `git` and nothing else executable, the open ledger in the prompt, `code` as its only peer |
| [`__main__.py`](../agent/improve/__main__.py) | CLI: same shape again, and with no task it runs the standing pass over the ledger |

**How one agent asks another for work: it runs it.** Every agent is a command,
and the coding agent already has `execute`. See [16. Delegation](16-delegation.md).

| File | The question it answers |
| --- | --- |
| [agent/delegation.py](../agent/delegation.py) | *Which agents may this one run, and how is it told?* — the one module that knows about all of them: the prompt paragraph naming each command, the probe that offers `explore` only if the pool can really search, and the `subprocess.run` `delegate_fix` uses |
| [agent/runtime/cli.py](../agent/runtime/cli.py) | *How does a command take its task?* — workdir plus `--task` or stdin, shared by all three, because `execute` has no stdin to pipe a brief into |
| [agent/code/gitstate.py](../agent/code/gitstate.py) | *Did the delegate actually do anything?* — what git says moved, rendered verdict-first, above whatever the session said about itself |

## 2.4 `evals/` — the measurement harness

Code lives with the code it measures; scenario *data* does not (see
[2.6](#26-related-repos) below).

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

## 2.5 Everything else

| Path | Role |
| --- | --- |
| [CLAUDE.md](../CLAUDE.md) | Agent entry point: north star, standards, index. Kept short on purpose. |
| [README.md](../README.md) | Human entry point: quick start and links out. Also short on purpose. |
| `docs/` | This wiki. The place to understand the system without reading source. |
| [.claude/settings.json](../.claude/settings.json) | The `Stop` hook that keeps this wiki from drifting ([12.4](12-development-harness.md#124-how-the-docs-stay-current)) |
| `.claude/reports/` | Deep one-off investigations, kept verbatim. |
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
- LangSmith runs — useful for watching, but they expire, which is precisely why
  the local trace exists ([7. Observability](07-observability.md)).

---

**Previous:** [← 1. Overview](01-overview.md) · **Next:** [3. The pool model →](03-pool-model.md)
