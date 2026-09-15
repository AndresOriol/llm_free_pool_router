# free_coding_agent

## North Star

**A standing maintainer:** an agent that works a project unattended for hours on
its own branch, keeps its documentation current, and leaves an account a human
reviews instead of reading the diff. The daily cycle and what it requires are in
[docs/design/long-run-harness.md](docs/design/long-run-harness.md).

What makes that affordable is the pool: multiple free-tier LLM plans behind one
router, which picks whichever account is available and fails over when one is
rate-limited, in cooldown, or erroring. The router is the substrate, not the
product — it is largely settled, and work on it now serves the hours the
maintainer has to stay up.

This is infrastructure, not a demo. It must be boring and reliable: a run should
never stall because one free account hit its limit.

**The agent is not bespoke.** There is one coding agent, [agent/code](agent/code/),
configured the way LangChain's `deepagents-code` configures one, with the pool as
its model. Its behaviour lives in Markdown, not Python.

## Non-goals

- Not a general-purpose LLM gateway or proxy product. One consumer: an agent
  loop, or a developer testing it locally.
- Not matching paid-tier quality or latency. Free tiers are the constraint, not
  a bug to work around with paid fallback.
- Not circumventing provider terms. Pooling free plans means legitimately
  holding several accounts, never bypassing per-account limits by deception.

## Layout

| Where | What it is |
| --- | --- |
| [llm_router](llm_router/) | One provider per account×model; picks the highest-priority one that is available and large enough. Selection only — it never makes a call. Config: [config.yaml](llm_router/config.yaml) |
| [llm_router/quota](llm_router/quota/) | How much free tier is left, read from the usage ledger. Advisory: it can skip a spent member, never stall a run ([14](docs/14-quota-panel.md)) |
| [agent/runtime](agent/runtime/) | The substrate: the failover loop as a `BaseChatModel`, the filesystem/exec jail, the pool's context floor, shared prompts, and the trace |
| [agent/code](agent/code/) | The coding agent ([6](docs/06-agent.md)) |
| [agent/explore](agent/explore/) | The web researcher; hands off by writing `/research/*.md` ([15](docs/15-explorer.md)) |
| [agent/improve](agent/improve/) | Reads recorded runs, names recurring failures as issues, delegates the fix. Cannot edit a file ([19](docs/19-improvement-agent.md)) |
| [agent/delegation.py](agent/delegation.py) | How one agent asks another: it runs it as a command ([16](docs/16-delegation.md)) |
| [agent/serve](agent/serve/) | The agents as HTTP endpoints, for containers ([18](docs/18-serving.md)) |
| [evals](evals/) | What decides whether a change helped: scenario runs and probes ([8](docs/08-evaluation-method.md), [20](docs/20-probes.md)). Scenarios live in the separate `agent_evals` repo |

Providers today: Groq, Gemini. The list is meant to grow ([5](docs/05-providers.md)).

## Docs

[docs/](docs/) is a wiki: numbered, concept-first pages explaining the logic
rather than the code. **Read it instead of the source** to understand the
system; read the source when you are about to change it. Start at
[docs/README.md](docs/README.md) — the index, and the conventions for adding a
page. [README.md](README.md) is the human entry point; this file is the agent's.
Both stay short and link into the wiki rather than growing inline.

An **artifact** here means a Markdown file in [.claude/artifacts/](.claude/artifacts/):
a plan, audit or work queue, written to be read once and acted on. Title, an
italic provenance line (branch, date, scope), a one-paragraph summary, then
numbered sections linking into the repo. They go stale by design — a finding
that outlives its artifact belongs in [docs/](docs/).

## Coding standards

- **The library is the harness.** `deepagents` ships the tools, the planner, the
  sub-agents, compaction, skills and the backends. Configure or write prose
  before writing Python; reimplementing what it provides is the failure mode
  this project is correcting for. Apply the **`deepagents` skill**
  ([.claude/skills/deepagents/](.claude/skills/deepagents/SKILL.md)) before
  touching any agent — it holds the extension ladder and the verified API.
- An agent's behaviour is Markdown. If the diff for "the agent should do X" is a
  `.py` file, the design has not been reduced yet.
- Keep router and provider logic obvious over clever — it runs unattended for
  hours. New providers implement `LLMProvider`
  ([base_provider.py](llm_router/base_provider.py)); never special-case one
  inside the router.
- No secrets in code or config. API keys are env vars only (`.env`, gitignored).
- Follow [ponytail](https://github.com/anthropics/skills) principles: the
  simplest thing that works, standard library over dependencies, no speculative
  abstraction. Apply the `ponytail` skill on non-trivial changes.

## Changing the harness

Changes to the harness (router config, prompts, backend, agent loop) are
evaluated, not argued. A candidate is a branch = one agent configuration, run
against scenarios and compared to baseline on quantitative metrics; a change
that cannot be shown to help does not merge. Protocol and metrics:
[8](docs/08-evaluation-method.md), [10](docs/10-metrics.md).

## Commits

- No AI or agent attribution — no "Co-Authored-By", no mention of a model or
  agent having made the change. Commits read as if a human wrote them.
- A short imperative summary line; a body only when the "why" is not obvious
  from the diff.
- One topic per commit. Split unrelated changes even when they were made back to
  back (docs and an unrelated config change are two commits).
