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

## 14.2 The path of one number

Nothing is computed twice and nothing is fetched at read time. Two writers put
files on disk; one reader joins them by provider name.

```
config.yaml ──limits──► loader.py ─────────────► .usage/pool.json ──┐
                                                                    │
  agent ──► RouterChatModel ──► the provider call                   ├──► build_report()
                  │                    │                            │     joins on the
                  └─ usage.record ─────┴──────► .usage/ledger.jsonl ┘     provider name
                       (one line per attempt)                              │
                                                                           ▼
                                                    table · --json · panel.html
```

- **What it cost us**: the failover loop calls `usage.record` on every attempt it
  makes, served or refused, naming the provider that served it.
- **What it is allowed to cost**: the loader writes the declared `limits` for the
  pool it just built, so the ceilings always match the running config.

`build_report` joins the two on the `provider` name (`GptOss120b_groq_1`), which
is why that name is generated in one place and never re-parsed. Reading is
side-effect free: `status` and `panel` open two files and touch no network, so an
agent may ask as often as it likes.

## 14.3 Two files, under `llm_router/.usage/`

Gitignored; `LLM_ROUTER_USAGE_DIR` moves the directory.

| File | Written by | Holds |
| --- | --- | --- |
| `ledger.jsonl` | [usage.py](../llm_router/usage.py), one line per attempt | `{ts, provider, account, platform, model, tokens_in, tokens_out, outcome, retry_after}` |
| `pool.json` | [loader.py](../llm_router/loader.py), on every pool build | the pool as configured, with each model's declared `limits` |

They move on different clocks, which is why they are not one file. The ledger
only grows. The snapshot is rebuilt from the pool the loader *actually built*, so
a model skipped for a missing key never appears as an idle account with quota to
spare, and an edited `config.yaml` cannot leave the panel measuring against
limits nobody is using.

`python -m llm_router` writes the snapshot without making a call, for the case
where the panel is opened before any agent has run.

Recording happens at the one place that knows which account served a call: the
failover loop in [`RouterChatModel`](../agent/router_chat_model.py). Token counts
are the provider's own numbers, never `estimate_tokens` — a panel reporting a
chars/4 guess as consumption would be worse than reporting nothing.

Writing to either file can never fail a run. The writers swallow their own
errors and log at debug: an unattended agent losing an afternoon's work to a full
disk, over a convenience, would be a self-inflicted wound.

## 14.4 One source, and what it misses

Every figure comes off the ledger, which counts what *this router* spent. The
account may have spent more: a key used from another machine, another checkout,
or by hand is invisible here. The panel says so rather than hiding it.

The alternative was tried and removed. Groq reports its remaining budget in
`x-ratelimit-*` headers, but only on a real completion — there is no usage
endpoint on either platform (`GET /models` carries nothing on Groq, and neither
Gemini endpoint carries anything at all). So a vendor reading cost a request per
member, went stale the moment it was taken, existed for one of the two
platforms, and put a second kind of number on a page whose whole job is to be
unambiguous. One source that is complete about what we did beats two sources
that disagree about what happened. The probe is in `git log`.

What is kept from that experiment is the part the vendor gives away for free: a
`Retry-After` on a refusal, recorded in the ledger at the moment it was said
([14.5](#145-windows-and-when-they-reset)).

## 14.5 Windows, and when they reset

A budget is assumed to work like this: **the first attempt opens the window, and
the window ends one length later**, whatever happens in between. So to find the
window in progress, look back one length from now and take the oldest attempt in
that span — that one opened it, and the budget resets one length after *it*.

That is also why `used` and `resets in` are read off the same span: every attempt
in the last minute belongs to one minute-window, because the oldest of them
opened it no earlier than a minute ago.

The assumption is not free, and it is wrong in one direction on purpose:

- A vendor running a **leaky bucket** (Groq's request budget refills
  continuously) clears earlier than this predicts.
- A vendor running a **calendar day** (Gemini resets at midnight Pacific) clears
  at a moment this cannot know.

Both make the panel say a budget is still spent when it may not be. It will not
promise headroom that isn't there, which is the only safe way to be wrong when
something unattended is about to start.

Where the provider told us better, that wins: a refusal carrying `Retry-After`
puts a **blocked for 33s** on the row, and no arithmetic of ours overrides it.

**Accounts do not share windows.** A model is fanned across every account on its
platform ([3.3](03-pool-model.md#33-the-fan-out)), so `gemini-3.5-flash` may be
three rows; each is a separate budget with its own clock. Nothing is ever summed
across accounts — a row is one account × one model, and the account rollup
aggregates rows *within* one account. Two Groq keys are two pools, even though
Groq meters each of them org-wide across its own models
([3.4](03-pool-model.md#34-priority-tiers)).

## 14.6 How a refused attempt is counted

A `rate_limited` line is an attempt the provider turned away for want of quota
([`is_rate_limited`](../llm_router/base_provider.py)). It is recorded, not
dropped, and then counted asymmetrically:

| | Counted? | Why |
| --- | --- | --- |
| Request gauges (RPM, RPD) | **yes**, and shown as *n refused* | It spent a request to be told no |
| Token gauges (TPM, TPD) | **no** | No tokens were spent being refused, and none are recorded |
| The window's start | requests only | A refusal opens the request window; the token window is opened by the oldest attempt that actually spent tokens |
| `Retry-After` | kept verbatim | The one statement about the future that isn't ours |

**Only an attempt the provider answered counts at all.** Two things that look
like requests are not:

- **A member the router skipped.** Size-based selection filters a member out
  before any call is made ([4.2](04-failover.md#42-size-aware-selection)); a
  request too large for `gpt-oss-120b` never reaches Groq, and nothing is
  written. The ledger holds attempts, not intentions.
- **An attempt that got no answer.** A timeout or a failed connection may never
  have left this machine. `reached_provider`
  ([base_provider.py](../llm_router/base_provider.py)) asks for positive
  evidence — an HTTP status, or the wording of a quota refusal — and without it
  the line is marked `reached: false`: still an error, never a request.

A 429 or Groq's 413 does count, because both are answers: the vendor read the
request and said no.

The asymmetry matters because the two numbers answer different questions. Thirty
requests where ten were refused is the same RPD as thirty that all worked, and a
completely different situation: the first is an account fighting its ceiling, the
second is an account using it. So the count is there in the gauge, and the
refusals are called out inside it.

It is also the reason a token figure can look low while an account is stuck: the
requests are being spent and the tokens are not.

## 14.7 What the report says

**The default view is per model, across every account that serves it.** That is
the question the panel was built for — *how much Gemini have I got left* — and
the reason it exists at all is to answer it without opening one provider console
per key. Capacity adds up, because each account is a separate budget: three keys
at 20 requests a day are 60 requests a day, and a ceiling that only some accounts
declare makes the total unknown rather than approximate. The reset shown is the
*soonest* of the accounts' windows, because that is when capacity next appears,
whichever key it appears on.

**The drill-down is per account**, member by member: once a model is running out,
that is where you see which key is spent and which still has room. Filtering
narrows the report itself rather than the rendering, so the JSON and the page
never disagree.

A platform is summed for what it spent and is deliberately given **no ceiling of
its own**. Groq meters one org-wide request budget across every model on an
account, so adding its per-model limits together would invent capacity that does
not exist ([3.4](03-pool-model.md#34-priority-tiers)).

Per entry, per window: requests, tokens, how much of each declared limit that is,
when the window resets, and how many of those requests were refused. A ceiling
folded from several accounts is shown as the sum it is — `2×5`, not a mystery 10.
The context window is a column of its own, for the same reason the router routes
by it ([4.2](04-failover.md#42-size-aware-selection)): a member that cannot hold
the job is not capacity, however much quota it has left. A limit the
vendor doesn't publish (Gemma's TPM) is *no ceiling*, not a zero one. The
**tightest** gauge is the one that will stop that entry first, which is rarely
the one you would guess: on Groq a step-heavy run hits TPM long before RPD.

Declared limits come from `limits:` in [config.yaml](../llm_router/config.yaml),
which is [5.4](05-providers.md#54-current-free-tier-limits) in a form a program
can read. **Update both when a vendor moves a limit**; the table is what a person
reads, the config is what the panel measures against.

## 14.8 Reading it

[`llm_router/quota/`](../llm_router/quota/) — Python, standard library only.

```bash
python -m llm_router.quota status                     # every model, over all its accounts
python -m llm_router.quota status --account groq_1    # one account, member by member
python -m llm_router.quota status --json              # the whole report, both views, as data
python -m llm_router.quota panel                      # writes the HTML, prints its path
```

`--json` exists for the coding agent driving this repo: one command, the whole
report, no scraping of a table meant for a person.

The panel is a **snapshot file, not a served page** — the question is asked once,
before a run, and a file has no port to collide with and no process left running
on a machine meant to be running agents.

It is still interactive, and the controls sit **inside the table they act on**,
one set per platform: the account buttons switch that platform between the model
view and one key's members, a checkbox hides what nothing touched today, a search
box filters by model name, and any column header sorts. A filter three headings
away from its table is a filter people forget is on.

Both views are written into the file and the script only hides rows, so the page
still reads with scripting off. Timestamps are local, with the offset spelled
out, because a panel can outlive the moment it was written.

This began as a TypeScript submodule and was ported. Nothing in it justified a
second toolchain in a Python repo: it is dict-reshaping and string templating,
and keeping the ledger's writer and its reader in one language matters more.

## 14.9 What it deliberately doesn't do

**It never gates a call.** The router routes on availability, not on arithmetic
against a budget: it learns an account is exhausted by being told so, and that
stays the mechanism ([4.4](04-failover.md#44-cooldown-and-backoff)). Routing off
a stored count would mean trusting our own arithmetic, over a window model we
know is approximate, against the provider's live answer — and being wrong in the
direction that stalls a run.

Spending the ledger on better routing — skipping a member the count says is
spent, rather than burning an attempt to find out — is a real option, and belongs
in [13.2](13-roadmap.md#132-what-to-do-next) if it is ever wanted. It is not this
page.

---

**Previous:** [← 13. Roadmap and scope](13-roadmap.md) · **Back to** [wiki index](README.md)
