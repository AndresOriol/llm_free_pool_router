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
failover loop in [`RouterChatModel`](../agent/utils/chat_model.py). Token counts
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

**A window is a bucket on the vendor's clock, not a span our first call opened.**
Google states it plainly: *"Requests per day (RPD) quotas reset at midnight
Pacific time."* That moment is fixed before anyone makes a request, and nothing
the pool does that afternoon moves it. Per-minute quota turns over the same way,
when the wall clock's minute does.

So `used` is what landed inside the bucket the clock is currently in, and
`resets in` is the time left on the clock — a fact about the calendar, and no
longer a fact about our history. [windows.py](../llm_router/quota/windows.py)
holds the two vendor facts this needs, both declared rather than measured
because a ledger cannot see them:

| Platform | The day turns at | The minute meter counts |
| --- | --- | --- |
| Gemini | midnight `America/Los_Angeles` | **input tokens** (Google's TPM is "tokens per minute (input)") |
| Groq | midnight UTC | the whole exchange |
| anything else | midnight UTC | the whole exchange |

### The model this replaced, and why it had to go

The panel used to assume the first attempt opened the window and the window
ended one length later. Against a calendar-day vendor that is not an
approximation, it is the wrong shape: a rolling day keeps counting yesterday
evening into this morning and never clears at all while a run keeps spending.
The ledger caught it doing exactly that — **50 calls Gemini served at moments
when the rolling count already read *spent***, and a single rolling 24-hour span
holding **35** served requests against a member declared at 20 a day.

That is not a harmless pessimism. [4.2.1](04-failover.md#421-skipping-a-member-whose-day-is-spent)
skips a member the ledger calls spent, so an arithmetic that says *spent* when
Google says *served* takes a working account out of the pool for hours — the one
error [14.9](#149-what-it-deliberately-doesnt-do) says this may never make.

Counting the vendor's day instead takes that 50 down to 26. It does not reach
zero, and it is not meant to: the rest is the filter's own 30-second cache
letting a burst through a ceiling it last read as open, which is
[budget.py](../llm_router/quota/budget.py)'s deliberate overspend and lands on
the retry path. A calendar window errs the same way — at worst it forgets a
spent day slightly early and the next attempt is refused, which is the path
failover already handles.

Where the provider told us better, that still wins: a refusal carrying
`Retry-After` puts a **blocked for 33s** on the row, and no arithmetic of ours
overrides it.

### Which instant a call is counted at

The bucket a call lands in is only as good as the timestamp deciding it, and
`ts` used to be written when a call **returned** — while a vendor meters it when
it **arrives**. For the day that is noise. For a minute it is not: a call taking
thirty seconds was recorded in a bucket it was never charged to, so per-minute
figures smeared across the boundary and a single minute could read high. Peaks
of eleven requests in one clock minute against a declared five came from this,
and dissolved when the same ledger was read over five minutes.

The caller now passes the moment it issued the request and
[usage.py](../llm_router/usage.py) dates the line by that. **Lines written
before that change still carry the completion time**, so a per-minute figure
read over the older half of a long ledger is still smeared — and a spike there
is not evidence that a published limit is stale.

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
| The window it lands in | both | A refusal and a served call sit in the same clock minute; what differs is what each one counts, not when either clears ([14.5](#145-windows-and-when-they-reset)) |
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

**It never gates a call**, and that is now a narrower claim than it was. The
*report* still gates nothing: no table, no JSON, no panel is on the path of a
request. But the ledger underneath it is read by one caller for one purpose —
[`budget.py`](../llm_router/quota/budget.py) tells the router which members have
spent their requests-per-day, and selection skips those
([4.2](04-failover.md#42-size-aware-selection)).

The original objection was that routing off a stored count means trusting our own
arithmetic, over a window model we know is approximate
([14.5](#145-windows-and-when-they-reset)), against the provider's live answer —
and being wrong in the direction that stalls a run. That last risk was real and
was being taken: the rolling day this used to count over held members out of the
pool that Google had already forgiven at midnight Pacific, fifty times over in
one recorded fortnight. Counting the vendor's day removes that particular way of
being wrong; it does not remove the objection, which is answered below. The objection was right and
is answered by construction rather than by better arithmetic: the count may only
*narrow* a choice, never make one. It picks among the members that already fit
the request; if it claims every candidate is spent it is ignored and the call
goes out anyway; and an unreadable ledger, a missing snapshot or an undeclared
limit all mean *available*. Every way it can be wrong ends in one refusal on the
retry path that has always handled refusals. None of them ends in a stall.

What changed is the price of being right. A daily ceiling is the one budget a
cooldown cannot express: the vendor refuses with a `Retry-After` of seconds, so
the member is back in the pool and asked again within the minute, and on
Gemini's twenty-a-day that is a fresh refusal every time round the loop until
midnight. Paying once to learn it, instead of all afternoon, is worth one
advisory read of a file we already write.

**Still off the table:** routing on tokens, on the per-minute windows, or on a
projection of what a run will cost. Those clear on their own, which is what a
cooldown already is.

---

**Previous:** [← 13. Roadmap and scope](13-roadmap.md) · **Back to** [wiki index](README.md)
