[← Wiki index](README.md)

# 4. Failover

*The core logic of the project: how one request finds a working account, and
what happens when it doesn't.*

## 4.1 The lifecycle of one request

```
estimate the request's size (chars/4 over messages + tool schemas)
repeat up to max_retries times:
    ask the router for the best provider that fits that size
    ├─ none available → sleep until the soonest cooldown expires (capped)
    │                    → retry, or stop if nothing is ever coming back
    ├─ call that provider's LangChain chat model
    ├─ success        → return
    └─ failure        → classify it
                        transient     → bench this provider, try the next
                        not transient → raise immediately, stop rerouting
raise "all providers exhausted"
```

Selection lives in [router.py](../llm_router/router.py); the loop lives in
[chat_model.py](../agent/runtime/chat_model.py). The split is deliberate:
the router only *chooses*, and has no `.generate()` of its own — giving it one
would force `llm_router` to import `agent` and invert the layering.

## 4.2 Size-aware selection

Selection takes an optional token estimate, and applies it **before** the
priority sort:

1. Filter to providers whose `check_availability()` is true.
2. Drop any provider whose ceiling the request would overflow (with a `0.9`
   safety margin — the estimate is rough, and on Groq the model's own output
   shares the same tokens-per-minute budget).
3. Among what's left, prefer the ones with **requests-per-day left**
   ([4.2.1](#421-skipping-a-member-whose-day-is-spent)).
4. Return the lowest `priority` among those.
5. If **nothing** fits, fall back to the largest available window and attempt
   the call anyway.

A provider with no declared ceiling is never filtered out.

**A caller may also declare a floor**, which is a claim about the *job* rather
than the request's size: `min_context` says "this work needs a member at least
this wide, even if today's message happens to fit a narrow one". By default the
floor is a **preference** — prefer a wide member, settle for a narrow one rather
than stall an unattended run behind a busy account.

`strict_context` makes it a **hard filter**, and the conversational harness needs
that ([6.5.1](06-agent.md#651-the-pool-drops-in-with-no-adapter)). Falling
through to an 8,000-token member there is not a degraded answer but a failed
call, and Groq's 100,000-tokens-per-day ceiling means those members could never
have served the request anyway. When nothing wide is free, selection returns
nothing and the caller waits — the right move when time is free and the
alternative could not have worked.

**Selection and the wait must be asked the same question.**
`seconds_until_available()` takes the same floor, because without it the two
disagree: selection refuses a narrow member while the wait reports "someone is
already available" — that same warm narrow member — so the loop stops waiting
and the run dies with a wide account seconds from returning.

**Why this exists.** A request structurally larger than a model's
tokens-per-minute limit will *never* fit that model. Benching it and retrying
later is futile. Before size-awareness, a large request would be rejected with
HTTP 413 by every small-TPM Groq account in turn, benching all of them, and
drain the whole pool down to Gemini's small daily quota — for a request that
was never going to fit any of them. Now a large request routes straight to a
high-capacity model.

Step 5 is a judgement call: a best-effort attempt is better than a stall, and
the resulting too-large error surfaces through the failure logging in
[4.7](#47-making-a-reroute-visible) rather than vanishing.

The estimate itself (`estimate_tokens`) is deliberately a chars/4 heuristic
with no tokenizer and no dependency. It only has to be good enough to keep a
request off a model it clearly overflows.

### 4.2.1 Skipping a member whose day is spent

Cooldown is how the pool learns an account is exhausted: ask, get refused, bench
it for as long as the refusal said ([4.4](#44-cooldown-and-backoff)). That works
because most free-tier limits are per minute — the wait is seconds, and the
member really is usable again afterwards.

**A daily ceiling breaks the mechanism.** Gemini refuses a run past 20
requests-per-day with a `Retry-After` measured in seconds, so the member leaves
cooldown, returns to the top of the priority order, is asked again, and is
refused again — every time round the loop, until midnight Pacific. The pool
spends its afternoon rediscovering a fact it recorded the first time.

So selection reads it back. `RpdBudget`
([quota/budget.py](../llm_router/quota/budget.py)) folds the usage ledger into
one set — the members whose `rpd` in [config.yaml](../llm_router/config.yaml) is
already spent in the last 24 hours — and step 3 passes over them silently. It is
one reading of a file the router already writes, reused for 30 seconds, so a
daily number is not re-parsed in front of every model call.

**It is advisory, and the design is what makes that true rather than a promise.**
The count is only what *this* router spent ([14.4](14-quota-panel.md#144-one-source-and-what-it-misses)),
over a window model that is approximate on purpose
([14.5](14-quota-panel.md#145-windows-and-when-they-reset)), possibly 30 seconds
stale. Three rules keep every one of those errors cheap:

- **It narrows, it never chooses.** The preference applies *within* the members
  that fit the request and clear the caller's floor, never across them. A member
  that cannot hold the job is not made preferable by having budget left.
- **A pool it calls entirely spent is ignored.** If no candidate has quota left,
  the count has said nothing useful, and every candidate is offered as before.
  Selection therefore never returns `None` for want of quota, and the wait in
  [4.5](#45-the-failover-loop) needs to know nothing about any of this.
- **Not knowing means available.** An unreadable ledger, a missing pool
  snapshot, a model with no published `rpd`, an exception anywhere in the read:
  all of them mean *route to it*.

Every way this can be wrong costs one refusal, on the path that has always
handled refusals. Being wrong the other way — refusing to route to a member the
vendor would have served — is the one that stalls an unattended run, and nothing
here can produce it. `LLM_ROUTER_RPD_FILTER=0` turns the whole thing off for an
A/B ([8. Evaluation method](08-evaluation-method.md)).

## 4.3 Classifying a failure

This is the piece most likely to bite silently, and the **order of the checks
is the design**. From [base_provider.py](../llm_router/base_provider.py):

| # | Check | Why it's at this position |
| --- | --- | --- |
| 1 | Rate-limit wording in the message (`rate_limit`, `too many requests`, `resource_exhausted`, `exceeded your current quota`) | Groq signals tokens-per-minute exhaustion with HTTP **413** and a `rate_limit_exceeded` body — not 429. Checking status first would read that as "your request is malformed" and treat the account as broken rather than temporarily over budget. Gemini's wrapper exposes no numeric status at all for `RESOURCE_EXHAUSTED`. Matching wording first catches both regardless of what status the vendor attached. |
| 2 | `tool_use_failed` / `tool call validation failed` | A per-model output glitch — small models sometimes emit tool arguments inside the tool *name*. Groq rejects it as HTTP **400**. Matched before the status check so the "other 4xx → fatal" rule below doesn't sweep it up; the next model usually formats it correctly. |
| 3 | Status code: 408, 409, 429, or any 5xx | Transient by the numbers. |
| 4 | Any other 4xx | **Not** transient. Bad request, bad auth, bad model name. Deliberately not retried: silently rerouting a malformed-request bug would burn the whole pool and hide the real bug behind "all providers exhausted". |
| 5 | Exception class name contains `timeout` or `connection` | Raw network failures that carry no status. |
| 6 | Anything else | Fatal. Raised, not swallowed. |

The bias throughout: **only** treat an error as "try another account" when it's
confidently a capacity or availability signal. Default to surfacing everything
else. An agent that reroutes past its own bugs is worse than one that stops.

## 4.4 Cooldown and backoff

When a provider fails transiently it is benched:

- If the provider sent a `Retry-After` header, that value is authoritative and
  used directly.
- Otherwise exponential backoff: `30 × 2^(failures−1)` seconds, **capped at
  300**.

The cap matters specifically for unattended runs. Without it, a provider that
keeps failing pushes its own retry window out to hours, effectively removing
itself from the pool for the rest of the session even after whatever caused the
failures has cleared.

## 4.5 The failover loop

`RouterChatModel` is a LangChain `BaseChatModel`, not a bespoke client class.
Two consequences follow from that one choice:

1. **Every provider SDK already speaks LangChain.** `ChatOpenAI` and
   `ChatGoogleGenerativeAI` already handle message formatting, tool-call
   parsing and streaming per vendor. A bespoke unified request/response shape
   across an OpenAI-compatible API and Gemini's own API would be
   re-implementing, worse, what those packages already do correctly.
2. **The pool becomes usable anywhere a `BaseChatModel` is accepted** — by
   LangGraph/deepagents, or anything else in the ecosystem.

There is exactly **one** failover code path. The smoke test does
`RouterChatModel(router=...).invoke(...)`; the agent hands the same object to
deepagents. Both get identical behaviour.

Two sizing decisions:

- **`max_retries` is relative to pool size**, not a constant. The agent builds
  it as `len(providers) + 3`, so a single step has enough budget to walk the
  *entire* pool once — the realistic worst case when every small-TPM model
  rejects a large request — plus a few spare attempts, instead of giving up
  partway through on an unlucky ordering.
- **The whole-pool wait is capped at 300s**, separately from the 300s cap
  inside cooldown. Two caps for two different failure modes: the provider-level
  cap bounds how long *one* account is benched; this one bounds how long a
  *single step* blocks when every account is benched at once, so the step
  re-checks periodically instead of sleeping past a provider that already
  recovered.

Only the synchronous `_generate` is implemented. Both entry points today are
synchronous, and async callers get `BaseChatModel`'s default `_agenerate`,
which runs `_generate` in a thread — fine for one agent at a time. A
hand-written async loop was written, found unused, and removed.

**Tool binding happens per call, after routing.** `bind_tools()` doesn't call
any SDK; it just stores the tools on a copy of the `RouterChatModel`, and the
binding is applied to whichever provider gets selected. It has to work this way
— the model actually serving the request isn't known until routing happens.

## 4.6 Known gaps

| Gap | Effect | Status |
| --- | --- | --- |
| A **decommissioned model** returns `404 model_not_found`. 404 is not in the transient set, so it propagates and kills the run. | Roughly half of measured runs died for a reason unrelated to the task. A dead model should be disabled *permanently* and skipped, the way a rate-limited one is skipped temporarily. | The top item in [13. Roadmap](13-roadmap.md#132-what-to-do-next); it's a candidate change like any other — branch it and measure it. |
| Cooldown state is per process. | Two agents on the same keys each rediscover which accounts are hot. | Accepted for now; see [13.4](13-roadmap.md#134-open-questions). |
| The pool gets walked hard for trivial work — ~10 provider calls and 5–7 distinct models for a one-line fix. | Efficiency numbers are hard to read until this is understood. | Under measurement, see [11. Evaluation status](11-eval-status.md). |

## 4.7 Making a reroute visible

A rerouted attempt is caught and swallowed inside the loop, so by default the
step records only the *successful* reroute and the failed attempt vanishes from
the trace. That would hide the most interesting signal in the whole system.

So failure handling closes the gap explicitly: it pulls the provider's
structured error body (Groq puts the model's raw malformed output in
`error.failed_generation`), logs it at WARNING, and — when a run manager is
present — attaches it to the router's own run via `on_text`. The *reason* a step
rerouted ends up in both the logs and the hosted trace, without depending on
the swallowed child attempt showing up on its own.

Provider calls are made with **no explicit config**, so they inherit the ambient
run context and nest naturally as child runs under the current agent step.
Passing a hand-built child callback manager was tried, doesn't improve the
nesting, and trips the tracer with "No indexed run ID" — so it is deliberately
not done. See [7. Observability](07-observability.md).

---

**Previous:** [← 3. The pool model](03-pool-model.md) · **Next:** [5. Providers and limits →](05-providers.md)
