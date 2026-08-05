[← Wiki index](README.md)

# 3. The pool model

*The vocabulary and the mental model. Read this before [4. Failover](04-failover.md);
everything there assumes these terms.*

## 3.1 Vocabulary

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

## 3.2 Why the atom is account × model, not account

An account is not a unit of capacity, because "am I rate-limited?" is answered
per model as often as per account. The same Groq key can be fine on one model
and over its tokens-per-minute budget on another. So the pool is built from
pairs, and each pair carries its own availability state.

This also gives you fallback-within-an-account for free: "try the 70B model
first, drop to a smaller model on the same key, only then move to a different
platform" is expressed entirely by priority numbers, with no code change.

## 3.3 The fan-out

The config does **not** list one entry per provider. It lists accounts and
models separately, and the loader multiplies them:

```
accounts:  groq_1, gemini_1
models:    Llama3_70b (groq), Gemini_3_5_Flash (gemini), ...
                 ↓
providers: Llama3_70b_groq_1, Gemini_3_5_Flash_gemini_1, ...
```

Every model is fanned out across every account on its platform
([loader.py](../llm_router/loader.py)). Adding a second Groq account therefore
gives *every* Groq model a second account instantly, without touching the model
list. This is the single most important property of the config format: the pool
is meant to grow by adding accounts, and that must stay a one-line change.

A provider whose account key is missing from the environment is **skipped with
a warning**, not raised on. A half-filled `.env` gets you a working smaller
pool rather than a program that refuses to start — which matters because
accounts get added incrementally.

## 3.4 Priority tiers

Priorities are grouped into four bands rather than a flat ordering, with gaps
so a new model can be slotted in without renumbering:

| Band | Range | Intent |
| --- | --- | --- |
| reasoning | 1–9 | Best judgment. Ambiguous or architectural steps. |
| mid | 10–19 | Solid for well-scoped implementation steps. |
| workhorse fallback | 20–29 | Weaker, but see the finding below — this tier does more real work than its name suggests. |
| last resort | 30+ | Only when everything above is cooling down. |

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
capacity, not a routing change ([13.3](13-roadmap.md#133-known-constraints-that-shape-the-roadmap)).

Within a band, order barely matters, for the same reason.

## 3.5 Availability is pull-based

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

## 3.6 Every pool member must support tool calling

The agent issues tool calls on every step and can reroute onto any pool member
mid-task, so a model that cannot accept a `tools` parameter would break a task
the moment it was selected. Groq's `compound` / `compound-mini` agentic systems
are excluded for exactly this reason.

This is a hard constraint on the pool used by the agent, and it is easy to
violate by accident when adding a model. See [5.5](05-providers.md#55-adding-a-model-or-account).

---

**Previous:** [← 2. Repo map](02-repo-map.md) · **Next:** [4. Failover →](04-failover.md)
