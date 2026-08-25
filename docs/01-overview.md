[← Wiki index](README.md)

# 1. Overview

*What this repo is trying to do, why it's shaped this way, and what it is not.*

## 1.1 The goal

Run a coding agent continuously without paying per token.

Free-tier LLM APIs are individually useless for an unattended agent: each one
has a low, oddly-shaped rate limit (requests/minute, tokens/minute,
tokens/day), and the moment an agent trips one, the run stops. The bet of this
repo is that *several* free accounts across *several* providers, pooled behind
one interface, add up to enough continuous capacity to keep an agent working —
because the accounts rarely all exhaust at the same instant.

So the product is a **router**: given a request, pick a free account that is
available right now, and when it fails, quietly move to the next one. An agent
running unattended for hours should never stall because one free account hit
its limit.

## 1.2 Why this is infrastructure, not a demo

The interesting engineering is not "call an LLM". It is everything around the
call:

- Deciding whether an error means *"this account is busy, try another"* or
  *"your code is broken, stop"* — and never confusing the two ([4. Failover](04-failover.md#43-classifying-a-failure)).
- Benching a failing account for the right amount of time, and letting it back
  in ([4.4](04-failover.md#44-cooldown-and-backoff)).
- Sending a large request to a model that can physically hold it, instead of
  discovering that by getting rejected eleven times ([4.2](04-failover.md#42-size-aware-selection)).
- Making the whole thing observable enough that you can tell *which* model
  actually did the work ([7. Observability](07-observability.md)).

Every one of those is a correctness problem with a boring right answer, which
is why the coding standard for this repo is "obvious control flow over
cleverness". The code has to survive hours of unattended running.

## 1.3 The three subsystems

The repo is three layers, each usable on its own, stacked:

| Layer | What it owns | Read |
| --- | --- | --- |
| **The pool** (`llm_router/`) | Which free account/model should serve the next request, and which ones are currently benched. Selection only — it never makes a call. | [3](03-pool-model.md), [4](04-failover.md), [5](05-providers.md) |
| **The agent** (`agent/`) | A LangGraph state machine of narrow roles whose single model is the pool. It reads and edits a jailed working directory, runs its own tests, and commits to its own branch. | [6](06-agent.md), [7](07-observability.md) |
| **The evaluation** (`evals/`) | Deciding whether a change to either of the above actually helped, by running scenarios and comparing distributions — not by argument. | [8](08-evaluation-method.md), [9](09-scenarios.md), [10](10-metrics.md), [11](11-eval-status.md) |

The layering is one-directional: `evals` drives `agent`, `agent` uses
`llm_router`, `llm_router` knows about neither. The one seam worth naming is
`RouterChatModel` — a LangChain `BaseChatModel` that wraps the pool so anything
in the LangChain ecosystem can consume it without knowing a router exists
([4.5](04-failover.md#45-the-failover-loop)).

## 1.4 What is deliberately not built

These are settled decisions, not gaps waiting to be filled. Re-opening one
needs a reason, not an opportunity.

- **Not a general-purpose LLM gateway.** No auth, no multi-tenancy, no billing,
  no admin UI. There is exactly one consumer: an agent loop, or the developer
  testing it.
- **Not chasing paid-tier quality.** Free tiers are the constraint the project
  exists to work within. "Just add a paid fallback" defeats the purpose.
- **No ToS circumvention.** Pooling means legitimately holding several free
  accounts. It does not mean evading a per-account rate limit through
  deception.
- **No dynamic scoring of providers.** Priority is a hand-assigned number.
  Latency/cost/success-rate scoring would add tuning surface with no evidence
  it's needed; cooldown already reacts to real-time availability
  ([3.4](03-pool-model.md#34-priority-tiers)).

## 1.5 Where the project actually stands

Honest summary, as of the last update to [11. Evaluation status](11-eval-status.md):

- The pool works and fails over in practice. A trivial one-line fix cost the
  conversational loop this replaced ~20 provider calls and ~227,000 input
  tokens — failover functions, but the pool gets walked hard, which is the whole
  reason the agent is shaped the way it is.
- The agent runs real tasks end to end, writes files, and runs its own tests.
- The evaluation harness runs end to end and has run its first real comparison.
  It still has **one scenario**, at the easiest level, and that scenario is now
  *exhausted as an instrument*: seven configurations were run against it and
  none was distinguishable from another. **Authoring L1/L2 scenarios is the
  single blocking item** ([13.2](13-roadmap.md#132-what-to-do-next)).
- Two Groq models are decommissioned and return `404 model_not_found`, which is
  not in the transient set, so it kills a run
  ([4.6](04-failover.md#46-known-gaps)). Evals work around it with a trimmed
  pool; the shipping pool does not.
- An alternative agent architecture lives unmerged on `harness/adhoc-router`:
  same task for 39× fewer tokens, but no demonstrated correctness gain
  ([6.12](06-agent.md#61-what-it-is)).

**Work in progress lives on a branch, not on `master`.** Check `git branch` before
assuming what is present.

## 1.6 Reading paths

| If you want to… | Read |
| --- | --- |
| understand the whole thing quickly | this page, then [2. Repo map](02-repo-map.md), then [4. Failover](04-failover.md) |
| add a free account or provider | [5. Providers](05-providers.md) |
| run the coding agent on a project | [6. The coding agent](06-agent.md) |
| change the router/prompt/loop and prove it helped | [8. Evaluation method](08-evaluation-method.md) → [12. Development harness](12-development-harness.md) |
| decide what gets built next | [13. Roadmap and scope](13-roadmap.md) |

---

**Next:** [2. Repo map →](02-repo-map.md)
