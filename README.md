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

Then run:

```bash
python llm_router/main.py
```

## Docs

- **New provider account, or want to add a provider?** →
  [docs/PROVIDERS.md](docs/PROVIDERS.md)
- **Curious how this project is built (agent roles, model tiers)?** →
  [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **Project goals and standards** → [CLAUDE.md](CLAUDE.md)
