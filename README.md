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
[Providers and limits](docs/pool/providers.md) for how to get a free key from each
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

See [The coding agent](docs/agents/code.md).

## Web explorer

The other half: an agent that researches the web and writes what it finds into
the project as Markdown, so the coding agent can read it later. Same pool, same
workdir, no shell — it runs on a plain `FilesystemBackend`, so it cannot run a
program at all.

```bash
echo "What are the current Gemini free-tier rate limits?" | python -m agent.explore ../my-project
```

It writes `/research/*.md` with a source URL beside every claim. See
[The web explorer](docs/agents/explore.md).

You can also let the coding agent ask for that itself, mid-task, instead of
running the two by hand. There is no protocol: it runs the same command you
would, `python -m agent.explore . --task "..."`, and reads back the final
message and the notes it wrote. A delegation costs a whole explorer session, so
it is worth knowing it is on — `AGENT_PEERS=` turns it off. See
[Delegation](docs/agents/delegation.md).

## The agent that improves the agents

A third one, whose project is the other two. It reads the runs they recorded,
names what keeps going wrong as an issue, hands the fix to the coding agent —
it cannot edit a file itself — and then checks whether the failure stopped
happening.

```bash
python -m agent.improve .          # work the ledger, in this repo
```

An issue closes only when runs recorded *after* its fix stop matching it, and
never because nobody looked. The ledger is `evals/results/issues/`. See
[The improvement agent](docs/agents/improve.md).

## Run it in a container

The same three agents, addressable over HTTP, for when the caller is not a
person at a terminal. The server runs the same command line you would:

```bash
cp .env.example .env      # keys, and SERVE_TOKEN=$(openssl rand -hex 32)
docker compose up --build
```

```bash
curl -sS -X POST localhost:8080/v1/agents/code/run \
  -H "Authorization: Bearer $SERVE_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"task": "Read NOTES.md and do what the newest feedback asks for",
       "workspace": "my-project"}'
```

You get **202** and a task id to poll at `/v1/tasks/<id>` — a run lasts hours,
so nothing waits on the request. An agent is bound to a *workspace*: a directory
under the mounted root, either mounted in from the host or cloned from a
repository URL you pass. See [Serving the agents](docs/operations/serving.md), and
[Deployment](docs/operations/deployment.md) for where this can actually run.

## Docs

Everything beyond the quick start lives in the wiki — start at
**[docs/README.md](docs/README.md)**, which indexes it.

- **Deploy this in a container, or call the agents as endpoints?** →
  [Serving the agents](docs/operations/serving.md)
- **New provider account, or want to add a provider?** →
  [Providers and limits](docs/pool/providers.md)
- **Have one agent ask another for work?** → [Delegation](docs/agents/delegation.md)
- **Run the coding agent on the pool?** → [The coding agent](docs/agents/code.md)
- **How does the router actually work, and why?** →
  [Failover](docs/pool/failover.md)
- **Curious how this project is built (agent roles, model tiers)?** →
  [Driving the free agents](docs/operations/driving-agents.md)
- **How much of the free tier is left?** →
  [Quota panel](docs/pool/quota.md) —
  `python -m llm_router.quota status`
- **Where is this going next?** → [Roadmap and scope](docs/status.md#roadmap-and-scope)
- **Project goals and standards** → [CLAUDE.md](CLAUDE.md)
