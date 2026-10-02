[← Wiki index](../README.md)

# Providers and limits

*The free-tier landscape the router is built around, the config that describes
it, and how to grow the pool.*

## Getting keys

**Groq** — free tier, fast inference, generous request limits.

1. [console.groq.com](https://console.groq.com) → sign up (Google/GitHub works).
2. **API Keys** → **Create API Key**.
3. Put it in `llm_router/.env` as `GROQ_API_KEY_1`.
4. For a second account: repeat with a different email, use `GROQ_API_KEY_2`.

**Google Gemini** — free tier via AI Studio.

1. [aistudio.google.com/apikey](https://aistudio.google.com/apikey) → sign in.
2. **Create API key** (choose "create in new project" if unsure).
3. Put it in `llm_router/.env` as `GEMINI_API_KEY_1`.
4. For additional accounts/keys: follow [Creating a dedicated email for additional Gemini keys](#creating-a-dedicated-email-for-additional-gemini-keys) to set up a secondary Google account and register `GEMINI_API_KEY_2`.

**Tavily Search** — search API for autonomous agents.

1. [tavily.com](https://tavily.com) → sign up / log in.
2. Go to **Overview** / **API Keys** → copy your API Key.
3. Put it in `llm_router/.env` as `TAVILY_API_KEY_1`.
4. For multiple accounts/keys for pool failover: add `TAVILY_API_KEY_2`, `TAVILY_API_KEY_3`, etc.


`llm_router/.env` is gitignored. Keys never go in code or in `config.yaml` —
config references an env var *pattern*, never a value.


### Creating a dedicated email for additional Gemini keys

To expand your Gemini key pool via Google AI Studio, each API key requires a separate Google account. If you need to create a new dedicated email account for this purpose:

1. **Create a Google Account**:
   - Go to [accounts.google.com/signup](https://accounts.google.com/signup).
   - Fill in your name, username (e.g., `yourproject.bot02@gmail.com`), and a strong password.
   - Complete the phone verification if required.
2. **Access Google AI Studio**:
   - Open a private/incognito window (or use a dedicated browser profile) to prevent account session conflicts.
   - Navigate to [aistudio.google.com/apikey](https://aistudio.google.com/apikey).
   - Sign in with the newly created Google account.
3. **Generate and store key**:
   - Click **Create API key** (select **Create API key in new project**).
   - Copy the generated API key.
   - Add it to your local `llm_router/.env` file with the next free number:
     ```env
     GEMINI_API_KEY_2=AIzaSy...
     ```
   - That is all: the loader picks up every `GEMINI_API_KEY_<n>` as an account
     named `gemini_<n>`, so `config.yaml` is not touched.

## Config schema

[config.yaml](../../llm_router/config.yaml) has two lists, and the loader
multiplies them ([The fan-out](model.md#the-fan-out)):

```yaml
accounts:                                     # one family per platform, not one entry per account
  - platform: groq                            # groups models to accounts
    type: openai_compatible                   # selects the adapter: openai_compatible | gemini
    url: https://api.groq.com/openai/v1       # v1 base, no /chat/completions
    api_key_env: GROQ_API_KEY_{n}             # pattern; every set GROQ_API_KEY_<n> is account groq_<n>

models:
  - name: Llama3_70b                          # label used in logs
    platform: groq                            # fans out to every account on this platform
    model: llama-3.3-70b-versatile            # the vendor's model id
    priority: 1                               # lower = tried first; see the tier bands
    max_input_tokens: 12000                   # min(TPM, context window)
    # temperature: 0.2                        # optional, this is the default
```

Every provider in the resulting pool is named `<model name>_<account name>`,
which is what appears in routing logs and in the trace.

`ROUTER_CONFIG` overrides the config path at runtime, so a configuration can
swap the whole model pool without touching the checked-in file
([What a configuration is](../evaluation/method.md#what-a-configuration-is)); unset, the default
is used.

Every eval configuration points it at `llm_router/config.yaml` **in the main
checkout**. The runner gives each arm its own worktree, so without this each arm
would draw from whatever pool its own commit happened to pin, and the pool would
be a confound in every comparison rather than a constant.

There used to be a second file here, `config.eval.yaml`: `config.yaml` minus the
models that were dead upstream, because Groq's `404 model_not_found` is not
transient and propagated far enough to kill a run. The router now retires a
member the platform says is gone and carries on with the rest
([Known gaps](failover.md#known-gaps)), so the trimmed pool had no job left —
and, being a copy maintained by hand, it had drifted into a staler pool than the
one actually shipping.

## Why `max_input_tokens` matters

It is not documentation. It is the number the router uses to decide whether a
request can physically fit a model before trying it
([Size-aware selection](failover.md#size-aware-selection)). Set it to
`min(tokens-per-minute, context window)`:

- On **Groq** the TPM limit is the bottleneck, and it is small — often well
  under the model's context window.
- On **Gemma** models TPM is unlimited, so the context window is the ceiling.

Omit it only when a limit is genuinely unknown; a provider with no ceiling is
never filtered out, which means a too-large request will be sent to it and fail.
**Update it whenever a vendor changes a limit** — a stale ceiling silently
degrades routing.

## Current free-tier limits

These are the numbers `max_input_tokens` is derived from. They change; treat
this table as a snapshot to re-check, not as truth.

**Google no longer publishes a per-model free-tier table.**
[The rate-limits page](https://ai.google.dev/gemini-api/docs/rate-limits) now
says limits "depend on a variety of factors (such as your usage tier) and can be
viewed in Google AI Studio", and sends you to
[the per-account dashboard](https://aistudio.google.com/rate-limit). So the
Gemini rows below are the last figures we held and cannot be re-derived from the
docs — they have to be read off AI Studio, once per key, and they may differ
between the six accounts. Third-party summaries of these limits disagree with
each other; none of them is a source worth writing into `config.yaml`, because a
*wrong* ceiling degrades routing further than a stale one.

Two things the page does still state, and the quota panel now models
([Windows, and when they reset](quota.md#windows-and-when-they-reset)):

- **RPD resets at midnight Pacific time** — a calendar day, not 24 hours after
  your first call.
- **TPM is "tokens per minute (input)"** — the reply is not charged against it.

They are also declared per model as `limits:` in
[config.yaml](../../llm_router/config.yaml), which is this table in a form a program
can read: the quota panel measures recorded usage against it
([Quota panel](quota.md)). **Update both together** — the table is
what a person reads, the config is what the panel believes.

### Groq

| Model | RPM | TPM | RPD | TPD |
| :--- | :---: | :---: | :---: | :---: |
| `openai/gpt-oss-20b` | 30 | 8K | 1K | 100K |
| `openai/gpt-oss-120b` | 30 | 8K | 1K | 100K |

Groq's Llama line (`llama-3.3-70b-versatile`, `llama-3.1-8b-instant`,
`meta-llama/llama-4-scout-17b-16e-instruct`) and `qwen/qwen3-32b` were retired
upstream and are gone from both this table and the config: all four answer
`404 model_not_found`.

The published per-model limits are misleading in one important way: in practice
Groq's free tier behaves as a **single shared request budget across every model
on the account**, so these do not add up to three independent pools. See
[Priority tiers](model.md#priority-tiers).

### Gemini

| Model | RPM | TPM | RPD |
| :--- | :--- | :--- | :--- |
| `gemini-3.8-flash` | 5 | 250K | 20 |
| `gemini-3.7-flash` | 5 | 250K | 20 |
| `gemini-3.6-flash` | 5 | 250K | 20 |
| `gemini-3.5-flash` | 5 | 250K | 20 |
| `gemini-3.5-flash-lite` | 15 | 250K | 500 |
| `gemini-3.1-flash-lite` | 15 | 250K | 500 |
| `gemini-3-flash-preview` | 5 | 250K | 20 |
| `gemini-2.5-flash-lite` | 10 | 250K | 20 |
| `gemma-4-31b-it` | 30 | 16K | 14.4K |
| `gemma-4-26b-a4b-it` | 30 | 16K | 14.4K |

Note the shape difference that drives the whole design: Groq gives you many
requests with tiny token budgets; Gemini gives you huge token budgets with very
few requests per day. Neither alone supports an agent. Together they mostly do.

## Adding a model or account

**A new account on an existing platform** — the cheap, high-value move:

1. Add the key to `.env` as the platform's pattern with the next number
   (`GEMINI_API_KEY_8`).

Done — no config change. Every model on that platform now has a second account behind it.

**A new model on an existing platform:**

1. Confirm it **supports tool calling** — non-negotiable
   ([Every pool member must support tool calling](model.md#every-pool-member-must-support-tool-calling)).
2. Add a `models:` entry with a priority inside the right tier band, and a
   `max_input_tokens` derived from its limits.
3. Update the limits table above.

## Adding a new platform

Candidates worth evaluating: Cerebras, OpenRouter's free models, Mistral's free
tier, HuggingFace Inference. The provider list is meant to grow.

1. Confirm it has a genuinely free tier, and read its rate limits and terms.
   Pooling means holding legitimate accounts — see
   [What is deliberately not built](../overview.md#what-is-deliberately-not-built).
2. If its request/response shape is OpenAI-compatible, you need **no code** —
   just an `accounts:` family with `type: openai_compatible`, the right `url`
   and a key pattern such as `CEREBRAS_API_KEY_{n}`.
3. Otherwise implement an `LLMProvider` subclass in
   [providers.py](../../llm_router/providers.py) and register it in the loader's
   type map. Do **not** special-case a provider inside the router.
4. Add its limits to this page.

Both existing adapters set `max_retries=0` on the underlying LangChain model, and
a new one must too. The SDK's own retry logic would retry against the *same*
dead account — exactly the job the router already owns one level up. Two retry
layers would either race or duplicate the cooldown decisions.
