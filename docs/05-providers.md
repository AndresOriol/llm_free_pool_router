[← Wiki index](README.md)

# 5. Providers and limits

*The free-tier landscape the router is built around, the config that describes
it, and how to grow the pool.*

## 5.1 Getting keys

**Groq** — free tier, fast inference, generous request limits.

1. [console.groq.com](https://console.groq.com) → sign up (Google/GitHub works).
2. **API Keys** → **Create API Key**.
3. Put it in `llm_router/.env` as `GROQ_API_KEY_1`.
4. For a second account: repeat with a different email, use `GROQ_API_KEY_2`.

**Google Gemini** — free tier via AI Studio.

1. [aistudio.google.com/apikey](https://aistudio.google.com/apikey) → sign in.
2. **Create API key** (choose "create in new project" if unsure).
3. Put it in `llm_router/.env` as `GEMINI_API_KEY_1`.
4. For a second account: different Google account, `GEMINI_API_KEY_2`.

`llm_router/.env` is gitignored. Keys never go in code or in `config.yaml` —
config references an env var *name*, never a value.

## 5.2 Config schema

[config.yaml](../llm_router/config.yaml) has two lists, and the loader
multiplies them ([3.3](03-pool-model.md#33-the-fan-out)):

```yaml
accounts:
  - name: groq_1                              # label used in logs
    user: someone@example.com                 # which signup this is, for your own tracking
    platform: groq                            # groups models to accounts
    type: openai_compatible                   # selects the adapter: openai_compatible | gemini
    url: https://api.groq.com/openai/v1       # v1 base, no /chat/completions
    api_key_env: GROQ_API_KEY_1               # name of the env var, never the key

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
([8.4](08-evaluation-method.md#84-what-a-configuration-is)); unset, the default
is used.

Every eval configuration points it at `llm_router/config.yaml` **in the main
checkout**. The runner gives each arm its own worktree, so without this each arm
would draw from whatever pool its own commit happened to pin, and the pool would
be a confound in every comparison rather than a constant.

There used to be a second file here, `config.eval.yaml`: `config.yaml` minus the
models that were dead upstream, because Groq's `404 model_not_found` is not
transient and propagated far enough to kill a run. The router now retires a
member the platform says is gone and carries on with the rest
([4.6](04-failover.md#46-known-gaps)), so the trimmed pool had no job left —
and, being a copy maintained by hand, it had drifted into a staler pool than the
one actually shipping.

## 5.3 Why `max_input_tokens` matters

It is not documentation. It is the number the router uses to decide whether a
request can physically fit a model before trying it
([4.2](04-failover.md#42-size-aware-selection)). Set it to
`min(tokens-per-minute, context window)`:

- On **Groq** the TPM limit is the bottleneck, and it is small — often well
  under the model's context window.
- On **Gemma** models TPM is unlimited, so the context window is the ceiling.

Omit it only when a limit is genuinely unknown; a provider with no ceiling is
never filtered out, which means a too-large request will be sent to it and fail.
**Update it whenever a vendor changes a limit** — a stale ceiling silently
degrades routing.

## 5.4 Current free-tier limits

These are the numbers `max_input_tokens` is derived from. They change; treat
this table as a snapshot to re-check, not as truth.

They are also declared per model as `limits:` in
[config.yaml](../llm_router/config.yaml), which is this table in a form a program
can read: the quota panel measures recorded usage against it
([14. Quota panel](14-quota-panel.md)). **Update both together** — the table is
what a person reads, the config is what the panel believes.

### Groq

| Model | RPM | TPM | RPD | TPD |
| :--- | :---: | :---: | :---: | :---: |
| `openai/gpt-oss-20b` | 30 | 8K | 1K | 100K |
| `openai/gpt-oss-120b` | 30 | 8K | 1K | 100K |
| `qwen/qwen3.6-27b` | 30 | 8K | 1K | 100K |

Groq's Llama line (`llama-3.3-70b-versatile`, `llama-3.1-8b-instant`,
`meta-llama/llama-4-scout-17b-16e-instruct`) and `qwen/qwen3-32b` were retired
upstream and are gone from both this table and the config: all four answer
`404 model_not_found`.

The published per-model limits are misleading in one important way: in practice
Groq's free tier behaves as a **single shared request budget across every model
on the account**, so these do not add up to three independent pools. See
[3.4](03-pool-model.md#34-priority-tiers).

### Gemini

| Model | RPM | TPM | RPD |
| :--- | :--- | :--- | :--- |
| `gemini-3.7-flash` | 5 | 250K | 20 |
| `gemini-3.6-flash` | 5 | 250K | 20 |
| `gemini-3.5-flash` | 5 | 250K | 20 |
| `gemini-3.5-flash-lite` | 15 | 250K | 500 |
| `gemini-3.1-flash-lite` | 15 | 250K | 500 |
| `gemini-3-flash-preview` | 5 | 250K | 20 |
| `gemini-2.5-flash` | 5 | 250K | 20 |
| `gemini-2.5-flash-lite` | 10 | 250K | 20 |
| `gemma-4-31b-it` | 15 | unlimited | 1.5K |
| `gemma-4-26b-a4b-it` | 15 | unlimited | 1.5K |

Note the shape difference that drives the whole design: Groq gives you many
requests with tiny token budgets; Gemini gives you huge token budgets with very
few requests per day. Neither alone supports an agent. Together they mostly do.

## 5.5 Adding a model or account

**A new account on an existing platform** — the cheap, high-value move:

1. Add the key to `.env` under a new env var name.
2. Add an `accounts:` entry with the same `platform`.

Done. Every model on that platform now has a second account behind it.

**A new model on an existing platform:**

1. Confirm it **supports tool calling** — non-negotiable
   ([3.6](03-pool-model.md#36-every-pool-member-must-support-tool-calling)).
2. Add a `models:` entry with a priority inside the right tier band, and a
   `max_input_tokens` derived from its limits.
3. Update the limits table above.

## 5.6 Adding a new platform

Candidates worth evaluating: Cerebras, OpenRouter's free models, Mistral's free
tier, HuggingFace Inference. The provider list is meant to grow.

1. Confirm it has a genuinely free tier, and read its rate limits and terms.
   Pooling means holding legitimate accounts — see
   [1.4](01-overview.md#14-what-is-deliberately-not-built).
2. If its request/response shape is OpenAI-compatible, you need **no code** —
   just an `accounts:` entry with `type: openai_compatible` and the right `url`.
3. Otherwise implement an `LLMProvider` subclass in
   [providers.py](../llm_router/providers.py) and register it in the loader's
   type map. Do **not** special-case a provider inside the router.
4. Add its limits to this page.

Both existing adapters set `max_retries=0` on the underlying LangChain model, and
a new one must too. The SDK's own retry logic would retry against the *same*
dead account — exactly the job the router already owns one level up. Two retry
layers would either race or duplicate the cooldown decisions.

---

**Previous:** [← 4. Failover](04-failover.md) · **Next:** [6. The coding agent →](06-agent.md)
