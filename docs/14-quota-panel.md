[← Wiki index](README.md)

# 14. Quota panel

*What the pool has spent, how close each free account is to its wall, and which
of the two possible sources that figure came from.*

## 14.1 The question

The router treats a rate limit as weather: reroute, cool the account down, carry
on ([4. Failover](04-failover.md)). That is the right behaviour mid-run and a
poor answer to the question asked before one — *is there enough left in these
accounts to work unattended this afternoon?* Nothing could answer it, because
nothing was writing down what had been spent, and the vendors' consoles answer
only one account per login.

## 14.2 Three files, under `llm_router/.usage/`

Gitignored; `LLM_ROUTER_USAGE_DIR` moves the directory.

| File | Written by | Holds |
| --- | --- | --- |
| `ledger.jsonl` | [usage.py](../llm_router/usage.py), one line per attempt | `{ts, provider, account, platform, model, tokens_in, tokens_out, outcome}` |
| `pool.json` | [loader.py](../llm_router/loader.py), on every pool build | the pool as configured, with each model's declared `limits` |
| `vendor.json` | [probe.py](../llm_router/quota/probe.py), when asked | what each vendor last said it had left |

They move on different clocks, which is why they are not one file. The ledger
only grows. The snapshot is rebuilt from the pool the loader *actually built*, so
a model skipped for a missing key never appears as an idle account with quota to
spare. The vendor readings are bought one at a time and go stale
([14.4](#144-the-probe)).

`python -m llm_router` writes the snapshot without making a call, for the case
where the panel is opened before any agent has run.

Recording happens at the one place that knows which account served a call: the
failover loop in [`RouterChatModel`](../agent/router_chat_model.py). Token counts
are the provider's own numbers, never `estimate_tokens` — a panel reporting a
chars/4 guess as consumption would be worse than reporting nothing.

**`outcome` is `ok`, `rate_limited` or `error`, and a refused attempt is recorded
rather than dropped.** It still spent a request against the account's budget, and
how often an account is being turned away is the most useful thing the ledger has
to say about a free tier.

Writing to any of these can never fail a run. The writers swallow their own
errors and log at debug: an unattended agent losing an afternoon's work to a full
disk, over a convenience, would be a self-inflicted wound.

## 14.3 Two sources, and they are not equal

The ledger counts what *this router* spent. The vendor counts what the *account*
spent — which is a different number the moment anything else touches the key:
another machine, another checkout, a script run by hand. Where a vendor reading
exists it wins, and every figure on the panel says which source it came from. A
panel that mixed them silently would be worse than one that only counted
locally, because it would look authoritative while being neither.

## 14.4 The probe

**There is no usage endpoint to ask.** Measured against both platforms on
2026-08-25:

| Request | Reports usage? |
| --- | --- |
| Groq `GET /v1/models` | no |
| Groq `POST /v1/chat/completions` | **yes** — `x-ratelimit-{limit,remaining,reset}-{requests,tokens}` |
| Gemini `GET /v1beta/models` | no |
| Gemini `:generateContent` | no — only `X-Gemini-Service-Tier` |

So a reading costs one request. `--probe` buys it deliberately: a one-token
completion per member that can answer, about 70 tokens and one request each
against budgets of 8,000/minute and 1,000/day. It is never implicit — an agent
polling `status --json` must not quietly spend the budget it is asking about.

A 429 answers too. A refused call carries the same headers, and an account that
has hit its wall is exactly when the reading is worth having.

**Which window a header describes is not in the header.** Groq's
`x-ratelimit-limit-requests` is the *daily* budget while `-limit-tokens` is the
*per-minute* one. Rather than hard-code that per platform — the special-casing
[the router refuses](../CLAUDE.md) — the reading is matched against the ceilings
the config declares, and a ceiling matching none of them is still shown under
the header's own name.

Two things fall out of probing that are worth knowing:

- **Groq's request budget is a leaky bucket, not a day.** Its reset header reads
  `1m26.4s` against a 1,000-request limit. A rolling 24-hour RPD window is the
  wrong shape for that, which is another reason the local count is a fallback.
- **A probe finds retired models for free.** Four of the seven Groq entries in
  the shipping config answered `404 model_not_found` — the same retirements that
  have killed runs three times ([4.3](04-failover.md#43-classifying-a-failure)).
  Reading the panel is a cheaper way to discover that than losing a session.

Google's free tier has no equivalent. Its quota is visible in the AI Studio
console and, programmatically, only through a GCP project's monitoring — a
service account and a different auth story, not something an AI Studio key can
do. Gemini members are recorded as *unable to report*, which reads differently on
the panel from *not asked yet*.

## 14.5 What the report says

Per account × model, per window: requests, tokens, and how much of each declared
limit that is, marked `vendor` where the vendor answered. A limit the vendor
doesn't publish (Gemma's TPM) is *no ceiling*, not a zero one — consumption is
still shown, just without a bar. The **tightest** gauge is the one that will stop
that member first, which is rarely the one you would guess: on Groq a step-heavy
run hits TPM long before RPD.

Totals are also rolled up per account, because that is where a free tier's real
budget lives — Groq meters one org-wide request pool across every model on the
account, so the per-model rows flatter it
([3.4](03-pool-model.md#34-priority-tiers)).

The local windows are rolling: RPM/TPM are the last 60 seconds, RPD/TPD the last
24 hours, always. Encoding each vendor's reset policy is a thing to get silently
wrong, and the error this choice makes has a safe direction — just after a
vendor's reset it still counts calls the vendor has forgiven, so it over-reports
and never claims headroom that isn't there.

Declared limits come from `limits:` in [config.yaml](../llm_router/config.yaml),
which is [5.4](05-providers.md#54-current-free-tier-limits) in a form a program
can read. **Update both when a vendor moves a limit**; the table is what a person
reads, the config is what the panel measures against.

## 14.6 Reading it

[`llm_router/quota/`](../llm_router/quota/) — Python, standard library only.

```bash
python -m llm_router.quota status            # the table
python -m llm_router.quota status --json     # the same report, as data
python -m llm_router.quota panel             # writes the HTML, prints its path
python -m llm_router.quota status --probe    # ask the vendors first
```

`--json` exists for the coding agent driving this repo: one command, the whole
report, no scraping of a table meant for a person.

This began as a TypeScript submodule and was ported. Nothing in it justified a
second toolchain in a Python repo: it is dict-reshaping and string templating,
the panel is a static file rather than an interactive front end, and keeping the
ledger's writer and its reader in one language matters more now that vendor
readings share the schema. The port is in `git log`.

The panel is a **snapshot file, not a served page**. Nothing here needs to be
live — the question is asked once, before a run — and a file has no port to
collide with, no process left running on a machine meant to be running agents,
and can be kept next to a run's results when a session is worth explaining later.

## 14.7 What it deliberately doesn't do

**It never gates a call.** The router routes on availability, not on arithmetic
against a budget: it learns an account is exhausted by being told so, and that
stays the mechanism ([4.4](04-failover.md#44-cooldown-and-backoff)). Routing off
a stored count instead would mean trusting our own arithmetic, or a reading from
ten minutes ago, over the provider's live answer — and being wrong in the
direction that stalls a run. The panel informs a human or an agent deciding what
to start; the failover loop is unchanged.

Spending the vendor readings on better routing — skipping a member whose last
reading says it is spent, rather than burning an attempt to find out — is a real
option, and belongs in [13.2](13-roadmap.md#132-what-to-do-next) if it is ever
wanted. It is not this page.

---

**Previous:** [← 13. Roadmap and scope](13-roadmap.md) · **Back to** [wiki index](README.md)
