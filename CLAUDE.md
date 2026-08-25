# free_coding_agent

## North Star

Enable a coding agent to run continuously without paying per-token, by pooling
multiple free-tier LLM provider plans behind a single router. The router picks
whichever free account/provider is currently available and falls over to the
next when one is rate-limited, in cooldown, or erroring.

This is infrastructure, not a demo. It must be boring and reliable: an agent
running unattended for hours should never stall just because one free account
hit its rate limit.

**Phase 2 (not started, direction only):** once the free pool is solid, wire
it as the model backend for [deep agents](https://github.com/langchain-ai/deepagents)
(LangGraph/LangSmith) to get a real autonomous coding agent loop running on
top of it. Do not start building the deep-agents integration until told to —
right now the router itself is the whole project.

## Non-goals

- Not a general-purpose LLM gateway/proxy product (no need for auth, multi-tenant
  billing, admin UI, etc.) — this serves one consumer: an agent loop, or the
  developer testing it locally.
- Not trying to match paid-tier quality/latency. Free tiers are the constraint,
  not a bug to work around with paid fallback.
- Not scraping/circumventing provider terms of service. "Pooling free plans"
  means legitimately holding multiple free accounts, not bypassing per-account
  rate limits through deception.

## Current state

- [llm_router](llm_router/) — holds one `LLMProvider` per account×model pair,
  filters to the ones available and large enough for the request, and returns
  the highest-priority one. Selection only; it never makes a call.
  Configured via [llm_router/config.yaml](llm_router/config.yaml).
- [llm_router/quota](llm_router/quota/) — answers "how much free tier is left",
  entirely from the usage ledger the router now writes: one line per model over
  every account serving it, filterable down to a single account. A terminal
  table, `--json` for an agent, and a static HTML panel. It reports; it never
  gates a call ([14. Quota panel](docs/14-quota-panel.md)).
- [agent](agent/) — `RouterChatModel` (the failover loop, as a LangChain
  `BaseChatModel`) plus a deepagents coding loop that runs on it, jailed to a
  workdir with `python`/`pytest` execution.
- [evals](evals/) — the harness that decides whether a change to either of the
  above helped. Scenarios live in the separate `agent_evals` repo.
- Providers today: Groq, Gemini. Expect more free-tier providers (Cerebras,
  OpenRouter free models, Mistral free tier, HuggingFace Inference, etc.) as
  they're evaluated — the provider list is meant to grow, not stay fixed.

## Docs

[docs/](docs/) is a wiki: numbered, concept-first pages that explain the logic
rather than the code. **Read it instead of the source** to understand the
system; read the source when you're about to change it. Start at
[docs/README.md](docs/README.md), which is the index and defines the wiki's
conventions — read it before adding a page.

[README.md](README.md) is the human entry point (quick start, links out) and
this file is the agent entry point (goal, standards, this index) — both stay
short and link into the wiki rather than growing inline.

Quick pointers: [4. Failover](docs/04-failover.md) for how the router works,
[14. Quota panel](docs/14-quota-panel.md) for what the accounts have spent,
[5. Providers](docs/05-providers.md) for accounts and limits,
[12. Development harness](docs/12-development-harness.md) for which model tier
does what, [13. Roadmap and scope](docs/13-roadmap.md) for what's next and
what's already settled.

## Coding standards

- Keep provider/router logic simple and boring — this code needs to survive
  unattended for hours, so prefer obvious control flow over cleverness.
- New providers implement the `LLMProvider` interface in
  [llm_router/base_provider.py](llm_router/base_provider.py); don't special-case
  a provider inside the router itself.
- No secrets in code or config — API keys are env vars only (see `.env`,
  gitignored).
- Follow [ponytail](https://github.com/anthropics/skills) principles: simplest
  solution that works, standard library over dependencies, no speculative
  abstraction. Apply the `ponytail` skill on non-trivial changes.

## Changing the harness

Changes to the agent harness (router config, system prompt, backend, agent
loop) are evaluated, not argued. Each candidate is a branch = one *agent
configuration*, run against scenario-based tests and compared to the baseline
on quantitative metrics; a change that can't be shown to help doesn't merge.
The protocol, metrics and promotion rule are in
[8. Evaluation method](docs/08-evaluation-method.md) and
[10. Metrics](docs/10-metrics.md). The runner and its results live in
[evals/](evals/); only the scenarios live in the separate `agent_evals` repo,
so they survive branch switching.

## Commits

- No AI/agent attribution in commit messages — no "Co-Authored-By", no
  mention of Claude or any model/agent having made the change. Commits read
  as if a human wrote them.
- Keep messages concise: a short imperative summary line; a body only when
  the "why" isn't obvious from the diff.
- Cluster changes by topic, one topic per commit. Don't bundle unrelated
  files into a single commit just because they changed in the same session —
  split them (e.g. docs changes and an unrelated config change go in
  separate commits, even if made back to back).
