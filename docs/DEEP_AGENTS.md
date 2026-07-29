# Running the coding agent

The [agent/](../agent/) package runs a [deepagents](https://github.com/langchain-ai/deepagents)
coding agent on the pooled free-tier router, so a task keeps going by switching
model/account when one hits its usage limit mid-run.

## Run it

```bash
pip install -r requirements.txt          # deepagents + langchain stack
python -m agent.coding_agent [workdir]
```

Or run it in one-shot mode by piping a task file:

```bash
python -m agent.coding_agent workdir < brief.md
```

- `workdir` is the directory the agent reads and edits (defaults to the current
  directory). The agent is **jailed to it** (`virtual_mode=True`): it cannot use
  absolute paths or `..` to reach the rest of the disk.
- The agent can **run its own tests** via the `execute` tool, but only
  `python`/`pytest` (no arbitrary shell) with `workdir` as the cwd — see
  `RestrictedShellBackend` in [restricted_backend.py](../agent/restricted_backend.py).
  This is a small blast radius, not a real sandbox (`python` is arbitrary code
  execution); for full isolation run the agent inside a container.
- Keys come from [llm_router/.env](../llm_router/.env) (see [PROVIDERS.md](PROVIDERS.md)).
- Type a task at the `>` prompt; `exit` to quit. Tool calls and the agent's
  replies stream to the terminal.

## How the failover works

The agent holds one model, `RouterChatModel`
([agent/router_chat_model.py](../agent/router_chat_model.py)), a LangChain
`BaseChatModel` fronting `AutonomousLLMRouter`. Every step routes to the
highest-priority available account; on a rate limit / 5xx / timeout that account
goes into cooldown and the step reroutes to the next one. So hitting a free-tier
limit mid-task is transparent — the next step just runs on another model.

Free tiers signal limits inconsistently (Groq uses HTTP 429 *and* 413 for
tokens-per-minute); both are treated as transient. A single agent step may walk
several small-TPM Groq models before a higher-limit account (e.g. Gemini)
accepts the request — this is expected.

## Tracing (LangSmith)

The whole stack is LangChain/LangGraph, so LangSmith tracing is native — no code
wiring, just env vars in [llm_router/.env](../llm_router/.env):

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<your key from smith.langchain.com>
LANGSMITH_PROJECT=free-coding-agent
```

Every agent run then shows up in the `free-coding-agent` project: the LangGraph
loop, each step's messages, and tool calls. The router calls the chosen
provider with no explicit config, so each attempt inherits the ambient run
context and is traced under the current step as the real model that served it
(`ChatOpenAI`/`ChatGoogleGenerativeAI` with the model name) — and a step that
walked several rate-limited accounts shows one failed provider run per attempt
before the one that succeeded, so failover is visible in the trace. Collect
these runs into datasets to evaluate and improve the agent. Leave
`LANGSMITH_API_KEY` blank to run with tracing off.

## Local trace (for evaluation)

LangSmith runs expire, so scoring a run later needs a local record. Set
`EVAL_TRACE_FILE` and the agent appends one JSON object per LLM/tool event to
that path:

```bash
EVAL_TRACE_FILE=run.jsonl python -m agent.coding_agent workdir < brief.md
```

Unset, nothing is attached and the agent behaves exactly as before. The handler
([agent/trace.py](../agent/trace.py)) is registered as an inheritable callback
on the run config, so it records the *provider* models the router delegates to,
not just the wrapper — a rerouted attempt shows up as an `llm_error` followed by
another `llm_start` on a different model, which is what makes failover
countable. Tool args and outputs are clipped to 2 KB. This file is the input to
every automatic metric in [EVAL.md](EVAL.md).

## Pool requirement

Every model in [config.yaml](../llm_router/config.yaml) must support tool
calling, because the agent issues tool calls on every step and can reroute onto
any pool member mid-task. Non-tool models (Groq `compound` / `compound-mini`)
are intentionally excluded — don't add a non-tool model to the pool used by the
agent.
