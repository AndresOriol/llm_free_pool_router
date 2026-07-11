# free_coding_agent

A router that pools multiple free-tier LLM provider accounts (Groq, Gemini,
more to come) behind one interface, so a long-running agent never stalls just
because one free account hit its rate limit — it fails over to the next
available account automatically.

## Quick start

```bash
pip install -r requirements.txt
```

Create `llm_router/.env` with your API keys (see
[docs/PROVIDERS.md](docs/PROVIDERS.md) for how to get a free key from each
provider):

```
GROQ_API_KEY_1=...
GEMINI_API_KEY_1=...
```

Then smoke-test the pool:

```bash
python tests/llm_router/smoke_test.py
```

## Coding agent

Run a [deepagents](https://github.com/langchain-ai/deepagents) coding agent on
the pool — it keeps working through free-tier limits by switching model mid-task:

```bash
python -m agent.coding_agent [workdir]
```

See [docs/DEEP_AGENTS.md](docs/DEEP_AGENTS.md).

## Docs

- **New provider account, or want to add a provider?** →
  [docs/PROVIDERS.md](docs/PROVIDERS.md)
- **Run the coding agent on the pool?** → [docs/DEEP_AGENTS.md](docs/DEEP_AGENTS.md)
- **How does the router actually work, and why?** →
  [docs/DESIGN.md](docs/DESIGN.md)
- **Curious how this project is built (agent roles, model tiers)?** →
  [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **Project goals and standards** → [CLAUDE.md](CLAUDE.md)
