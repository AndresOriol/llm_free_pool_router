# How the router works, and why

This is the internal design doc: what each module does, how a request flows
through the system, and the reasoning behind the non-obvious choices. Read
this instead of the source when you need to understand the system, not just
operate it — [PROVIDERS.md](PROVIDERS.md) and [DEEP_AGENTS.md](DEEP_AGENTS.md)
cover setup/usage, this covers logic.

## The problem this is solving

Free-tier LLM APIs each have low, differently-shaped rate limits (requests/min,
tokens/min, tokens/day). A single account stalls an unattended agent the
moment it hits one. The fix is to hold several accounts across several
providers and treat the whole set as one pool: always route to *some*
available account, and only fail when literally all of them are exhausted at
once. Everything below exists to make that pool selection boring and
correct — see the North Star in [CLAUDE.md](../CLAUDE.md).

## Module map

```
llm_router/
  base_provider.py   LLMProvider ABC: per-account state (available? cooldown?)
                     + is_transient(): classifies exceptions into
                       "retry elsewhere" vs "real error, raise"
  providers.py        Two LLMProvider subclasses: OpenAICompatibleProvider
                       (Groq, or anything OpenAI-shaped) and GeminiProvider
  loader.py           Reads config.yaml + .env, builds one LLMProvider per
                       config entry, skips entries with a missing key
  router.py           AutonomousLLMRouter: filters to available providers,
                       returns the highest-priority one
  config.yaml         One entry per account; see PROVIDERS.md for the schema

agent/
  router_chat_model.py  RouterChatModel(BaseChatModel): the failover loop
                          itself — try highest-priority provider, on transient
                          failure cooldown it and try the next, repeat
  coding_agent.py        Wires RouterChatModel into a deepagents loop with a
                          filesystem backend

tests/
  llm_router/
    smoke_test.py         One prompt through the pool, to sanity-check that
                            keys/config.yaml are wired up correctly
    test_is_transient.py  Framework-free self-check for is_transient()'s
                            reroute-vs-raise branches (python -m ...)
```

Each file has exactly one job, and the split maps directly onto a question you
might ask: "is this account usable right now" (`base_provider`), "which SDK do
I call for this provider type" (`providers`), "how do accounts get built from
config" (`loader`), "which account is best right now" (`router`), "what
actually happens on a call, including retry" (`router_chat_model`).

## Why a LangChain `BaseChatModel`, not a bespoke client

`RouterChatModel` subclasses `langchain_core`'s `BaseChatModel` instead of
being a standalone router class with a `.generate()` method. Two things follow
from that one choice:

1. **Every provider SDK already speaks LangChain.** `ChatOpenAI` and
   `ChatGoogleGenerativeAI` already handle message formatting, tool-call
   parsing, and streaming per-provider. Building a bespoke unified request/
   response shape across an OpenAI-compatible API and Gemini's own API would
   just be re-implementing what these packages already do correctly.
2. **The router becomes usable anywhere a `BaseChatModel` is accepted** —
   directly by LangGraph/deepagents ([coding_agent.py](../agent/coding_agent.py)),
   or by anything else in the LangChain ecosystem. This is why `agent/`
   exists at all: `AutonomousLLMRouter` picks *which* account, `RouterChatModel`
   is *how* that choice shows up as a normal chat model to callers.

There's exactly one failover code path. Both entry points wrap a router in a
`RouterChatModel` and call it: the smoke test
([tests/llm_router/smoke_test.py](../tests/llm_router/smoke_test.py)) does
`RouterChatModel(router=...).invoke(...)`, the agent hands the same object to
deepagents. `AutonomousLLMRouter` only
selects; it deliberately has no `.generate()` of its own (that would make
`llm_router` import `agent`, inverting the layering).

## Provider state machine

Each `LLMProvider` ([base_provider.py](../llm_router/base_provider.py)) is a
tiny state machine with three fields: `is_available`, `cooldown_until`,
`consecutive_failures`.

- **`check_availability()`** is pull-based, not a background timer: it's
  called right before each routing decision, and flips `is_available` back to
  `True` once `cooldown_until` has passed. No polling thread, no shared
  scheduler — state converges lazily exactly when someone asks.
- **`trigger_cooldown(retry_after)`** blocks the account. If the provider gave
  a `Retry-After` header, that's authoritative and used directly. Otherwise:
  exponential backoff, `30 * 2^(failures-1)`, capped at 300s. The cap matters
  for an unattended run — without it, a provider that keeps failing would push
  its own retry window out to hours, effectively removing it from the pool
  for the rest of the session even after whatever caused the failures clears
  up.
- **`consecutive_failures` resets to 0** the moment a provider is observed
  available again (in `check_availability`), not after a success. A single
  bad account shouldn't need a good call to "forgive" it — surviving its
  cooldown is enough, so the next failure starts the backoff fresh rather than
  compounding indefinitely.

## Classifying failures: `is_transient()`

This is the piece most likely to bite silently, so it's worth understanding
the ordering in [base_provider.py](../llm_router/base_provider.py):

1. **String-match on the message first, before status code.** Groq signals
   tokens-per-minute exhaustion with HTTP **413** ("Request too large") and a
   `rate_limit_exceeded` body — not 429. If status were checked first, this
   would be misread as a permanent "your request is malformed" error and the
   account would be treated as broken rather than temporarily over budget.
   Matching the rate-limit wording first catches this regardless of which
   status code a given provider happens to attach to it.
2. **Then status code**: 408/409/429 or any 5xx is transient (server hiccup,
   conflict, or rate limit by the numbers). Any other 4xx is treated as a real
   client error — bad request, bad auth, bad model name — and is **not**
   retried elsewhere, on purpose: silently rerouting a malformed-request bug
   would burn through the whole pool and hide the actual bug behind
   "all providers exhausted."
3. **Then exception class name** for `timeout`/`connection` errors that don't
   carry a status at all (raw network failures).
4. **Anything else is fatal.** An unrecognized exception with no status and no
   familiar name is assumed to be a real bug, and is raised rather than
   swallowed. The design bias throughout is: only treat an error as "try
   another account" when it's confidently a capacity/availability signal;
   default to surfacing anything else.

## Selection: `AutonomousLLMRouter.get_best_provider()`

Filter providers to those where `check_availability()` is `True`, return the
one with the lowest `priority` (`min(...)`). `priority` is a manually-assigned
number in `config.yaml` — lower tried first. This is deliberately not scored
by anything dynamic (latency, cost, success rate): priority order is set once
per config to encode "prefer the fast/cheap/high-limit model first," and the
cooldown mechanism is what actually reacts to real-time availability. Keeping
selection this simple is a direct application of the "boring and reliable"
standard in [CLAUDE.md](../CLAUDE.md) — a scoring model would add tuning
surface with no evidence it's needed yet.

## The failover loop: `RouterChatModel._generate`

Only the sync `_generate` is implemented, because both entry points today are
synchronous (the smoke test's `.invoke()` and the coding agent's
`agent.stream(...)`). Async callers get `BaseChatModel`'s default `_agenerate`,
which runs `_generate` in a thread — good enough for one agent at a time (a
`ponytail:` comment on the class names that ceiling). A hand-written async loop
was removed as unused; re-add a real `_agenerate` only if high async
concurrency ever matters. The loop:

```
for up to max_retries attempts:
    provider = router.get_best_provider()
    if no provider available:
        wait until the soonest cooldown expires (capped at 300s), then retry
        (or stop if nothing is ever coming back)
    call provider's underlying LangChain model
    on success: return the result
    on failure: classify with is_transient()
        transient  -> cooldown this provider, try the next attempt
        not transient -> raise immediately, do not keep rerouting
raise "all providers exhausted" if the loop runs out of attempts
```

Two sizing decisions worth knowing:

- **`max_retries` is set relative to pool size**, not a fixed constant. The
  coding agent builds it as `len(providers) + 3`
  ([coding_agent.py](../agent/coding_agent.py)) so a single step has enough
  budget to walk the *entire* pool once (every small-TPM Groq model rejecting
  a large request) plus a few spare attempts, rather than giving up partway
  through the pool on a single unlucky ordering.
- **The whole-pool-cooldown wait is capped at 300s** (`_MAX_WAIT_SECONDS` in
  [router_chat_model.py](../agent/router_chat_model.py)), independent of the
  300s cap already inside `trigger_cooldown`. Two different caps for two
  different failure modes: the provider-level cap bounds how long one bad
  account is benched; this one bounds how long a single agent step blocks
  when *every* account is benched at once, so a step re-checks periodically
  instead of potentially sleeping past a provider that already recovered.

`bind_tools()` doesn't call any provider's SDK — it just stores the tools/
kwargs on the `RouterChatModel` instance (`model_copy`) so whichever provider
gets picked at call time has `.bind_tools()` applied to *it*
(`_underlying()`). Tool binding has to happen per-call, after routing, because
the model actually serving the request isn't known until routing happens.

## Tracing: why failover is visible in LangSmith

`_child_config()` passes `run_manager.get_child()` as the `callbacks` config
into the underlying provider's `.invoke()`/`.ainvoke()`. This nests the
provider's actual call as a child run under the router's own run in
LangSmith, so a trace shows not just "the agent called a model" but which
account/model served each step — including the point where a step rerouted
after a cooldown. Without this, tracing would show a single opaque "router"
node and failover would be invisible. See
[DEEP_AGENTS.md](DEEP_AGENTS.md#tracing-langsmith) for how to turn tracing on.

## Config and provider construction

`loader.load_providers_from_config()` ([loader.py](../llm_router/loader.py))
is intentionally simple: read YAML, look up each entry's `api_key_env` in the
environment (after loading `llm_router/.env`), skip the entry with a warning
if the key or provider `type` is missing, else construct the matching
`LLMProvider` subclass. Skipping rather than raising means a partially-filled
`.env` still gets you a working (smaller) pool instead of blocking the whole
program on one missing key — useful since accounts get added incrementally.

The account/model split matters: one `config.yaml` entry is one
account+model pair, so the same Groq account appears multiple times at
different priorities for different models
([config.yaml](../llm_router/config.yaml)) — this is how "try the 70B model
first, fall back to smaller/cheaper models on the same key before moving to a
different provider" is expressed, without any code change.

`OpenAICompatibleProvider` and `GeminiProvider`
([providers.py](../llm_router/providers.py)) both set `max_retries=0` on the
underlying LangChain model. This is deliberate: the SDK's own retry logic
would retry against the *same* dead account, which is exactly the job the
router already owns at a higher level. Double retry logic would either race
against or duplicate the router's cooldown/reroute decisions.

## The coding agent's use of the router

[coding_agent.py](../agent/coding_agent.py) builds one `RouterChatModel` and
hands it to `deepagents.create_deep_agent` as the model for the whole
LangGraph loop — every step of every task routes through the same failover
logic, which is the entire point: a rate limit mid-task reroutes transparently
instead of stopping the run. `FilesystemBackend(virtual_mode=True)` jails the
agent to `workdir` (no absolute paths, no `..` escape) since this agent
actually writes to a real filesystem, unlike the smoke test. See
[DEEP_AGENTS.md](DEEP_AGENTS.md) for how to run it; this section is only about
why it's wired this way.
