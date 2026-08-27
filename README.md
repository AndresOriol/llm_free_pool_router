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
echo "Read NOTES.md and do what the newest feedback asks for" | python -m agent.code ../my-project
```

See [docs/06-agent.md](docs/06-agent.md).

## Web explorer

The other half: an agent that researches the web and writes what it finds into
the project as Markdown, so the coding agent can read it later. Same pool, same
jail, no shell.

```bash
echo "What are the current Gemini free-tier rate limits?" | python -m agent.explore ../my-project
```

It writes `/research/*.md` with a source URL beside every claim. See
[docs/15-explorer.md](docs/15-explorer.md).

You can also let the coding agent ask for that itself, mid-task, instead of
running the two by hand: it gets one `delegate` tool, the explorer answers with
the notes it wrote, and the vocabulary on the wire is
[A2A](https://a2a-protocol.org)'s rather than something invented here. A
delegation costs a whole explorer session, so it is worth knowing it is on —
`AGENT_PEERS=` turns it off. See
[docs/16-agent-protocol.md](docs/16-agent-protocol.md).

## Docs

Everything beyond the quick start lives in the wiki — start at
**[docs/README.md](docs/README.md)**, which indexes it.

- **New provider account, or want to add a provider?** →
  [5. Providers and limits](docs/05-providers.md)
- **Have one agent ask another for work?** → [16. The agent protocol](docs/16-agent-protocol.md)
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
