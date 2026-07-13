# Getting free provider accounts

Each entry in [llm_router/config.yaml](../llm_router/config.yaml) is one
account on one provider, referenced by an `api_key_env` name that must exist
in [llm_router/.env](../llm_router/.env) (copy from `.env.example` if present,
or create it — it's gitignored). This doc walks through getting a free API
key for each provider currently wired up, and how to slot it in.

After adding a key, add a matching entry to `config.yaml`:

```yaml
- name: <label for logs>
  type: openai_compatible   # or: gemini
  url: <provider endpoint>
  model: <model id>
  api_key_env: <ENV_VAR_NAME_YOU_ADDED>
  priority: <lower = tried first>
```

Multiple accounts on the same provider are fine and expected — give each its
own env var (e.g. `GROQ_API_KEY_1`, `GROQ_API_KEY_2`) and its own
`config.yaml` entry at the same priority, so the router rotates between them.

## Groq

Free tier, generous rate limits, fast inference.

1. Go to [console.groq.com](https://console.groq.com) and sign up (Google/GitHub
   login works).
2. Open **API Keys** in the left nav → **Create API Key**.
3. Copy the key into `llm_router/.env` as `GROQ_API_KEY_1` (or the next free
   number if you already have one).
4. To add another account, repeat with a different email/Google account and
   use `GROQ_API_KEY_2`, etc.

### Límites de los Modelos Groq

| Modelo | RPM | TPM | RPD | TPD |
| :--- | :---: | :---: | :---: | :---: |
| `llama-3.1-8b-instant` | 30 | 6K | 14.4K | 100K |
| `llama-3.3-70b-versatile` | 30 | 12K | 1K | 100K |
| `meta-llama/llama-4-scout-17b-16e-instruct` | 30 | 30K | 1K | 100K |
| `openai/gpt-oss-20b` | 30 | 8K | 1K | 100K |
| `openai/gpt-oss-120b` | 30 | 8K | 1K | 100K |
| `qwen/qwen3-32b` | 60 | 6K | 1K | 100K |
| `qwen/qwen3.6-27b` | 30 | 8K | 1K | 100K |

## Google Gemini

Free tier via Google AI Studio.

1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey) and
   sign in with a Google account.
2. Click **Create API key** (choose "create in new project" if unsure).
3. Copy the key into `llm_router/.env` as `GEMINI_API_KEY_1`.
4. To add another account, repeat with a different Google account and use
   `GEMINI_API_KEY_2`, etc.

### Límites de los Modelos Gemini
| Modelo | RPM | TPM | RPD |
| :--- | :--- | :--- | :--- |
| `gemini-3.5-flash` | 5 | 250K | 20 |
| `gemini-3.1-flash-lite` | 15 | 250K | 500 |
| `gemini-3-flash-preview` | 5 | 250K | 20 |
| `gemini-2.5-flash` | 5 | 250K | 20 |
| `gemini-2.5-flash-lite` | 10 | 250K | 20 |
| `gemma-4-31b-it` | 15 | Ilimitado | 1.5K |
| `gemma-4-26b-a4b-it` | 15 | Ilimitado | 1.5K |

## Adding a new provider

If you want to add a provider not listed here (e.g. Cerebras, OpenRouter free
models, Mistral, HuggingFace Inference):

1. Confirm it has a genuinely free tier and check its rate limits/ToS.
2. Implement an `LLMProvider` subclass in [llm_router/providers.py](../llm_router/providers.py)
   if its request/response shape isn't already covered by
   `OpenAICompatibleProvider` or `GeminiProvider`.
3. Add a section to this doc with signup steps, and an entry to
   `config.yaml`.
