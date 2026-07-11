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

- [llm_router](llm_router/) — async router that holds a list of `LLMProvider`
  instances (one per API key/account), filters by availability, sorts by
  priority, and retries across the pool on failure. See
  [llm_router/router.py](llm_router/router.py), [llm_router/providers.py](llm_router/providers.py),
  [llm_router/base_provider.py](llm_router/base_provider.py), configured via
  [llm_router/config.yaml](llm_router/config.yaml).
- Providers today: Groq (multiple accounts), Gemini. Expect more free-tier
  providers (Cerebras, OpenRouter free models, Mistral free tier, HuggingFace
  Inference, etc.) to be added as they're evaluated — the provider list is
  meant to grow, not stay fixed.

For the full agentic development harness (which model tier does what, how
skills fit in), see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Docs structure

[README.md](README.md) is the human entry point (quick start, links out) and
this file is the agent entry point (goal, standards, this index) — both stay
short and link into `docs/` rather than growing inline.

Everything else — how `docs/` itself is organized, what goes where, and who
maintains it — is defined in [docs/README.md](docs/README.md). That file is
the source of truth for the docs structure across the whole project, not
just the current llm_router stage; read it before adding a new doc.

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
