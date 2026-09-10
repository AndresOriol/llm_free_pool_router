# free_coding_agent

## North Star

Enable a coding agent to run continuously without paying per-token, by pooling
multiple free-tier LLM provider plans behind a single router. The router picks
whichever free account/provider is currently available and falls over to the
next when one is rate-limited, in cooldown, or erroring.

This is infrastructure, not a demo. It must be boring and reliable: an agent
running unattended for hours should never stall just because one free account
hit its rate limit.

**Phase 2 (in progress):** a **standing maintainer** — an agent that works a
project unattended for hours on its own branch, updates its documentation, and
writes an account a human reviews instead of the code. The daily cycle and what
it requires are argued in
[docs/design/long-run-harness.md](docs/design/long-run-harness.md).

**The agent is not bespoke.** There is one coding agent, [agent/code](agent/code/):
a conversation configured the way LangChain's `deepagents-code` configures one,
with the pool as its model. The narrow-role harness it replaced is deleted.

The constraint that produced the narrow-role design has been lifted for coding
work: a session now routes only to members holding at least 128,000 input
tokens, so splitting work to fit an 8,000-token member is no longer a
requirement to design around. Groq stays in the pool for work that fits it.

**This was a maintenance decision, not a measurement.** The deleted arm won the
last cost comparison 39× over, on inputs that no longer hold and that nobody
re-ran. `tokens_in` per run is the number that says whether that was a
mistake ([6.1.1](docs/06-agent.md#611-the-arm-that-was-deleted)).

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
  filters to the ones available and large enough for the request, prefers those
  with requests-per-day left, and returns the highest-priority one. Selection
  only; it never makes a call.
  Configured via [llm_router/config.yaml](llm_router/config.yaml).
- [llm_router/quota](llm_router/quota/) — answers "how much free tier is left",
  entirely from the usage ledger the router now writes: one line per model over
  every account serving it, filterable down to a single account. A terminal
  table, `--json` for an agent, and a static HTML panel. The report gates
  nothing; one reader of the same ledger tells the router which members have
  spent their day, so a daily ceiling is skipped rather than rediscovered by
  refusal — advisory, and never able to stall a run
  ([4.2.1](docs/04-failover.md#421-skipping-a-member-whose-day-is-spent),
  [14. Quota panel](docs/14-quota-panel.md)).
- [agent/runtime](agent/runtime/) — the substrate: `RouterChatModel` (the
  failover loop, as a LangChain `BaseChatModel`), the filesystem jail with
  `python`/`pytest`/`git` execution, the tools over it, and the trace. Knows
  nothing about sessions.
- [agent/code](agent/code/) — the coding agent: `create_deep_agent` over that
  jailed backend, with the configuration ported from `deepagents-code`
  ([prompt.py](agent/code/prompt.py), [context.py](agent/code/context.py),
  [shell.py](agent/code/shell.py)). Its record is one LangSmith run tree
  ([trace.py](agent/code/trace.py)) plus the `EVAL_TRACE_FILE` JSONL every
  metric is summed over.
- [agent/explore](agent/explore/) — the web researcher: LangChain's
  deep-research agent, ported close to verbatim, on the pool. An orchestrator
  plans and delegates to a `research-agent` sub-agent and never searches itself;
  `tavily_search` finds URLs through a pool of Tavily accounts, fetches each page
  and converts it, so the **page** reaches the model rather than a summary of it;
  `think_tool` forces a pause between searches. Its tool surface is tailored
  rather than inherited: no `ls`, `glob`, `grep` or shell, because it never
  explores a repository — a caller names the paths, and `research_status` says
  what its own research holds. The framework's prompt sections about the tools
  it does not have are removed with them, which is half of a 49% cut in what
  every call carries before the conversation starts
  ([15.5.1](docs/15-explorer.md#1551-the-surface-is-chosen-not-inherited)).
  Which directory it writes to is named per run, so one investigation can
  continue another. A run ends by reading its own report back against the
  request and saving what it found as `review.md`; the session asks a second
  time if it skipped that
  ([15.5.4](docs/15-explorer.md#1554-the-review-at-the-end)). It hands off to
  the coding agent by writing `/research/*.md`
  ([15. The web explorer](docs/15-explorer.md)). Every deviation from upstream is
  marked `ADAPTED` in [deep_prompts.py](agent/explore/deep_prompts.py).
- [agent/improve](agent/improve/) — the agent whose project is the other
  agents, modelled on [LangSmith
  Engine](https://docs.langchain.com/langsmith/engine). It reads the runs this
  project has recorded — eval runs, which carry a hidden-test verdict, and live
  ones, which do not — names what recurs as an *issue* in
  `evals/results/issues/`, hands the fix to the coding agent over A2A, and
  re-checks the issue's signature against runs recorded afterwards, closing it
  or reopening it. **It cannot edit a file**
  ([readonly.py](agent/improve/readonly.py)): the agent that diagnoses is not
  the agent that changes the code, which is what makes a diff reviewable against
  a diagnosis written before it. An issue never closes because nobody looked
  ([19. The improvement agent](docs/19-improvement-agent.md)). **Unmeasured** —
  `IMPROVE_FIX=0` is the diagnose-only arm.
- [agent/protocol](agent/protocol/) — how one agent asks another for work. The
  vocabulary is [A2A](https://a2a-protocol.org)'s — `AgentCard`, `Task`,
  `Message`, `Artifact` — and must not drift from it; the transport is a local
  Python call, so a delegate shares its caller's cooldown and trace. The coding
  agent gets one `delegate` tool and a directory of cards in its prompt; the
  deliverable is still a file on disk that the protocol only points at
  ([16. The agent protocol](docs/16-agent-protocol.md)). **Unmeasured** — it is
  a configuration, and `AGENT_PEERS=` turns it off for the A/B.
- [agent/serve](agent/serve/) — the agents as HTTP endpoints, for running this
  in a container. A2A's methods over `http.server`: a caller POSTs a `Message`
  to `/v1/agents/<name>/message:send`, gets **202** and a task id, and polls —
  a run lasts hours and every platform in front kills a request in minutes.
  What an agent is bound to is a workspace *name* resolved under one mounted
  root, never a path from the request, and the server clones a repository into
  a new one on request. Exactly one worker, because per-process cooldown and a
  JSONL ledger both say so. Nothing here changes an agent
  ([18. Serving](docs/18-serving.md)).
- [evals](evals/) — the harness that decides whether a change to the above
  helped. Scenarios live in the separate `agent_evals` repo. Two instruments:
  a **scenario run** is the acceptance contract (minutes, one bit, decided by
  hidden tests), and a **probe** is the small end — the real agent in front of
  one situation, stopped at its first decision, one model call
  ([20. Probes](docs/20-probes.md), `python -m evals probes`). A probe cannot
  say a task was solved; it says the agent started the way it should, which is
  where the recorded failures live. Probes are defined in
  [evals/probes/](evals/probes/) with the failure each one guards cited beside
  it, and pushed one-way to a LangSmith dataset — the files are the claim, the
  dataset is a projection.
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

**"Artifact" in this repo means a markdown file in
[.claude/artifacts/](.claude/artifacts/)** — a plan, an audit, or a queue of
work, written to be read once and acted on, not a wiki page. Ask for one and
that is what to produce: a title, an italic provenance line naming the branch,
the date and the scope, a one-paragraph version up front, then numbered
sections that link back into the repo with relative paths. They are working
documents, so they go stale; a finding that outlives its artifact belongs in
[docs/](docs/) instead.

Quick pointers: [18. Serving](docs/18-serving.md) for the container and the
endpoints, [4. Failover](docs/04-failover.md) for how the router works,
[14. Quota panel](docs/14-quota-panel.md) for what the accounts have spent,
[16. The agent protocol](docs/16-agent-protocol.md) for agent-to-agent
delegation, [19. The improvement agent](docs/19-improvement-agent.md) for the
loop that turns recorded runs into fixes,
[5. Providers](docs/05-providers.md) for accounts and limits,
[12. Development harness](docs/12-development-harness.md) for which model tier
does what, [15. The web explorer](docs/15-explorer.md) for web research, [13. Roadmap and scope](docs/13-roadmap.md) for what's next and
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
