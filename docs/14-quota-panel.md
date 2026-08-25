[← Wiki index](README.md)

# 14. Quota panel

*What the pool has spent, and how close each free account is to its wall.*

## 14.1 The question

The router treats a rate limit as weather: reroute, cool the account down, carry
on ([4. Failover](04-failover.md)). That is the right behaviour mid-run and a
poor answer to the question asked before a run — *is there enough left in these
accounts to work unattended this afternoon?* Nothing could answer it, because
nothing was writing down what had been spent. The consoles could, one login per
account, which is not an answer anyone gets twice.

So: a ledger the router appends to, and a reader over it.

## 14.2 Two files, written by the router

[`llm_router/usage.py`](../llm_router/usage.py) owns both, under
`llm_router/.usage/` (gitignored; `LLM_ROUTER_USAGE_DIR` moves it).

| File | Written | Holds |
| --- | --- | --- |
| `ledger.jsonl` | one line per attempt, appended | `{ts, provider, account, platform, model, tokens_in, tokens_out, outcome}` |
| `pool.json` | rewritten on every pool build | the pool as configured, with each model's declared `limits` |

They are separate because they move on different clocks. The ledger only grows;
the snapshot is rebuilt by [`loader.py`](../llm_router/loader.py) from the pool
it *actually built*, so a model skipped for a missing key never shows up as an
idle account with quota to spare, and an edited `config.yaml` cannot leave the
panel measuring against limits nobody is using.

The snapshot normally appears as a side effect of the router loading. Opening
the panel before any agent has run would otherwise show consumption with nothing
to measure it against, so `python -m llm_router` builds the pool and writes the
snapshot without making a single call.

Recording happens at the one place that knows which account served a call: the
failover loop in [`RouterChatModel`](../agent/router_chat_model.py). Token counts
are the provider's own numbers, never `estimate_tokens` — a panel reporting a
chars/4 guess as consumption would be worse than reporting nothing.

**`outcome` is `ok`, `rate_limited` or `error`, and a refused attempt is recorded
rather than dropped.** It still spent a request against the account's budget,
and how often an account is being turned away is the most useful thing the panel
has to say about a free tier.

Writing to either file can never fail a run. Both writers swallow their own
errors and log at debug: an unattended agent losing an afternoon's work to a
full disk, over a convenience, would be a self-inflicted wound.

## 14.3 Why not ask the provider

Groq returns `x-ratelimit-remaining-*` headers; Gemini returns nothing
comparable, and the LangChain wrapper hides what it does return
([4.3](04-failover.md#43-classifying-a-failure) has the same complaint about
status codes). A panel built on headers would be exact for one platform and
blank for the other, and would need per-provider code in a place where
[the standing rule](../CLAUDE.md) is not to special-case a provider. Our own
record covers every platform identically and costs one appended line per call.

The price is honest and worth stating: **the ledger knows only what went through
this router.** A call made from another machine, or by hand against the same
account, is invisible here.

## 14.4 The windows are rolling, and the vendors' are not

Gemini resets its daily quota on its own clock (midnight Pacific); Groq's day
starts whenever Groq says. Encoding a timezone and a reset policy per platform
is a thing to get silently wrong, so the report doesn't: RPM/TPM are the last
60 seconds and RPD/TPD the last 24 hours, always.

The error this introduces has a direction. Just after a vendor's reset the
rolling window still counts calls the vendor has already forgiven, so the panel
reads **higher** than the vendor does. It over-reports consumption and never
claims headroom that isn't there — the only direction that is safe for someone
deciding whether to start something long.

## 14.5 What the report says

Per account × model, per window: requests, tokens, and how much of each declared
limit that is. A limit the vendor doesn't publish (Gemma's TPM) is *no ceiling*,
not a zero one — the consumption is still shown, just without a bar. The
**tightest** gauge is the one that will stop that member first, which is rarely
the one you'd guess: on Groq a step-heavy run hits TPM long before RPD.

Totals are also rolled up per account, because that is where a free tier's real
budget lives — Groq meters one org-wide request pool across every model on the
account, so the per-model rows flatter it
([3.4](03-pool-model.md#34-priority-tiers)).

Limits come from `limits:` in [config.yaml](../llm_router/config.yaml), which is
the same table as [5.4](05-providers.md#54-current-free-tier-limits) in a form a
program can read. **Update both when a vendor moves a limit**; the table is prose
and the config is what the panel measures against.

## 14.6 Reading it

[`llm_router/quota/`](../llm_router/quota/) — TypeScript, no runtime
dependencies, no build step (Node strips the types and runs the sources).

```bash
node llm_router/quota/src/cli.ts status          # the table
node llm_router/quota/src/cli.ts status --json   # the same report, as data
node llm_router/quota/src/cli.ts panel           # writes the HTML, prints its path
```

`--json` exists for the coding agent driving this repo: one command, the whole
report, no scraping of a table meant for a person.

The panel is a **snapshot file, not a served page**. Nothing here needs to be
live — the question is asked once, before a run — and a file has no port to
collide with, no process left running on a machine meant to be running agents,
and can be committed next to a run's results when a session is worth explaining
later.

## 14.7 What it deliberately doesn't do

**It never gates a call.** The router routes on availability, not on arithmetic
against a budget: it learns an account is exhausted by being told so, and that
stays the mechanism ([4.4](04-failover.md#44-cooldown-and-backoff)). Routing off
a local count instead would mean trusting our own arithmetic over the provider's
answer, and being wrong in the direction that stalls a run. The panel informs a
human or an agent deciding what to start; the failover loop is unchanged.

Spending the ledger on better routing — skipping a member the count says is
already spent, rather than burning an attempt to find out — is a real option, and
belongs in [13.2](13-roadmap.md#132-what-to-do-next) if it is ever wanted. It is
not this page.

---

**Previous:** [← 13. Roadmap and scope](13-roadmap.md) · **Back to** [wiki index](README.md)
