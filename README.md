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
[docs/05-providers.md](docs/05-providers.md) for how to get a free key from each
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

Point the agent at a project and give it a task on stdin. It works the project
unattended on its own branch, and keeps going through free-tier limits by
switching model mid-task:

```bash
echo "Read NOTES.md and do what the newest feedback asks for" | python -m agent.harness ../my-project
```

See [docs/06-agent.md](docs/06-agent.md).

## Docs

Everything beyond the quick start lives in the wiki — start at
**[docs/README.md](docs/README.md)**, which indexes it.

- **New provider account, or want to add a provider?** →
  [5. Providers and limits](docs/05-providers.md)
- **Run the coding agent on the pool?** → [6. The coding agent](docs/06-agent.md)
- **How does the router actually work, and why?** →
  [4. Failover](docs/04-failover.md)
- **Curious how this project is built (agent roles, model tiers)?** →
  [12. Development harness](docs/12-development-harness.md)
- **How much of the free tier is left?** →
  [14. Quota panel](docs/14-quota-panel.md) —
  `python -m llm_router.quota status`
- **Where is this going next?** → [13. Roadmap and scope](docs/13-roadmap.md)
- **Project goals and standards** → [CLAUDE.md](CLAUDE.md)
