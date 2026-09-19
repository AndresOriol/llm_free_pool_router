[← Wiki index](../README.md)

# The pool model

*The vocabulary and the mental model. Read this before [Failover](failover.md);
everything there assumes these terms.*

## Vocabulary

| Term | Meaning |
| --- | --- |
| **platform** | A vendor: `groq`, `gemini`. Determines which SDK adapter is used. |
| **account** | One signup on one platform, identified by an API key env var. Several accounts per platform is the entire point. |
| **model** | A model id offered by a platform, e.g. `llama-3.3-70b-versatile`, with a priority and a token ceiling. |
| **provider** | One **account × model** pair. This is the atom the router selects between — the thing that can be available or benched. |
| **pool** | Every provider that was successfully constructed at startup. |
| **priority** | A hand-assigned integer. Lower is tried first. Shared by every account serving that model. |
| **cooldown** | A provider is benched until a timestamp, after a failure. |
| **ceiling** (`max_input_tokens`) | The largest request this provider can physically accept: `min(tokens-per-minute, context window)`. |

## Why the atom is account × model, not account

An account is not a unit of capacity, because "am I rate-limited?" is answered
per model as often as per account. The same Groq key can be fine on one model
and over its tokens-per-minute budget on another. So the pool is built from
pairs, and each pair carries its own availability state.

This also gives you fallback-within-an-account for free: "try the 70B model
first, drop to a smaller model on the same key, only then move to a different
platform" is expressed entirely by priority numbers, with no code change.

## The fan-out

The config does **not** list one entry per provider. It lists accounts and
models separately, and the loader multiplies them:

```
accounts:  groq_1, gemini_1, gemini_2
models:    Llama3_70b (groq), Gemini_3_5_Flash (gemini), ...
                 ↓
providers: Llama3_70b_groq_1, Gemini_3_5_Flash_gemini_1,
           Gemini_3_5_Flash_gemini_2, ...
```

Every model is fanned out across every account on its platform
([loader.py](../../llm_router/loader.py)). This is the single most important
property of the config format: the pool is meant to grow by adding accounts,
and that must stay a one-line change.

It has been exercised once. Adding `gemini_2` — six lines, no model touched —
took every Gemini model from one account to two, and the pool from 14 providers
to 21. That is the whole mechanism working as intended, and it is what makes
running a scenario at n=5 affordable rather than extravagant.

A provider whose account key is missing from the environment is **skipped with
a warning**, not raised on. A half-filled `.env` gets you a working smaller
pool rather than a program that refuses to start — which matters because
accounts get added incrementally.

## Priority tiers

Priorities are grouped into four bands rather than a flat ordering, with gaps
so a new model can be slotted in without renumbering:

| Band | Range | Intent |
| --- | --- | --- |
| reasoning | 1–9 | Best judgment. Ambiguous or architectural steps. |
| mid | 10–19 | Solid for well-scoped implementation steps. |
| workhorse fallback | 20–29 | Weaker, but see the finding below — this tier does more real work than its name suggests. |
| last resort | 30+ | Only when everything above is cooling down. |

### Ordering inside a band

Within a band the order is capability first **until latency says otherwise**.
The reasoning band is led by `gemini-3.6-flash`, then `3.7`, then `3.5`, with
`openai/gpt-oss-120b` behind them at 4.

`3.7` is the newer and stronger model and still sits second, because a loop that
makes a call per step pays for latency on every one of them. Measured
2026-08-25: `3.7` answered in 33–54s and returned `503 UNAVAILABLE`
("experiencing high demand") once in four calls, against 0.9s for `3.6`. It
spends output budget thinking before it writes — asked for five output tokens it
returned `MAX_TOKENS` and no text at all. Strongest-first is the right default;
40× slower and one refusal in four is what overrides it. The tier exists
for judgement over a wide view, and gpt-oss-120b cannot hold one — its 8,000
token ceiling is a tenth of what the Geminis take
([Why `max_input_tokens` matters](providers.md#why-max_input_tokens-matters)). It stays in the band
because the flash models run out fast: 20 requests a day each, which a working
afternoon spends.

The lite models are ordered the same way inside the workhorse band —
`3.5-flash-lite` ahead of `3.1`, ahead of `2.5` — behind `gpt-oss-20b`, which
keeps that slot because a well-scoped mechanical step usually fits its window
and Groq answers faster.

### The finding that produced the tiers

This came out of reading actual traces, not from taste. On any task longer than
roughly ten steps:

- **Groq's free tier behaves as one shared org-wide request budget across every
  model on the account**, not as six independent pools. A burst of steps
  exhausts the entire Groq side within seconds, regardless of which Groq model
  is tried first.
- The stronger Gemini models get hit hard enough to 429/503 too.
- What is left standing with headroom is `gemini-3.1-flash-lite`, which then
  ends up serving most of the real implementation work on long tasks **by
  exhaustion, not by choice**.

Tiering does not fix that fallthrough. It only decides who is tried first while
capacity still exists — which means the early steps of every run, and the whole
of every short run, get the pool's best judgment instead of whatever happened to
be first in the config file. Fixing the fallthrough itself needs more account
capacity, not a routing change ([Known constraints that shape the roadmap](../status.md#known-constraints-that-shape-the-roadmap)).

Within a band, order barely matters, for the same reason.

## Availability is pull-based

A provider is a three-field state machine: `is_available`, `cooldown_until`,
`consecutive_failures`.

There is no background timer and no scheduler thread. `check_availability()` is
called immediately before each routing decision and flips a provider back to
available once its cooldown has passed. State converges lazily, exactly when
somebody asks. For an unattended process this is strictly better than polling:
nothing to start, nothing to leak, nothing to get out of sync.

`consecutive_failures` resets when a provider is observed available again —
**not** on a successful call. Surviving the cooldown is enough to be forgiven,
so the next failure starts backoff fresh instead of compounding indefinitely. A
single unlucky account shouldn't need a good call to escape an ever-growing
penalty.

## Every pool member must support tool calling

The agent issues tool calls on every step and can reroute onto any pool member
mid-task, so a model that cannot accept a `tools` parameter would break a task
the moment it was selected. Groq's `compound` / `compound-mini` agentic systems
are excluded for exactly this reason.

This is a hard constraint on the pool used by the agent, and it is easy to
violate by accident when adding a model. See [Adding a model or account](providers.md#adding-a-model-or-account).
