[← Wiki index](../README.md)

# Observability

*What a run leaves behind, and why the answer is changing.*

## Why two

Watching a run and scoring it later are different questions with different
lifetimes.

| | LangSmith | The durable record |
| --- | --- | --- |
| Answers | *What is this run doing right now?* | *What exactly happened in this run, forever?* |
| Lifetime | Expires | On disk, for as long as the run directory is kept |
| Cost | A hosted dependency | None |
| Used by | A human eyeballing a live run | Every automatic metric in [Metrics](metrics.md) |

The rule that followed was **LangSmith is for watching, never for the record** —
a verdict must rest entirely on files on disk, because hosted traces expiring
would silently invalidate old comparisons.

**The requirement stands; the rule that implemented it was too strong.** What
expiry actually forbids is *depending on the hosted copy at scoring time*. It
does not forbid asking LangSmith for the tree once, while it still exists, and
writing it down. That distinction is what [The record: one run tree](#the-record-one-run-tree)
takes, and it is the difference between snapshotting a structure that already
exists and rebuilding it by hand from callbacks.

So a run leaves **two** records, and both are load-bearing. The flat JSONL
([The local trace](#the-local-trace)) is what every automatic metric is summed over and
is written whether or not LangSmith is reachable; the fetched tree
([The record: one run tree](#the-record-one-run-tree)) is what a human or a post-mortem agent
actually reads.

## LangSmith

The whole stack is LangChain/LangGraph, so tracing is native — no code wiring,
just env vars in `llm_router/.env`:

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<key from smith.langchain.com>
LANGSMITH_PROJECT=free-coding-agent
```

Leave the key blank to run with tracing off.

What you get: the LangGraph loop, each step's messages, and tool calls. And
critically, **failover is visible**: because the router calls the chosen
provider with no explicit config, each attempt inherits the ambient run context
and is traced as a child run showing the *real* model that served it
(`ChatOpenAI` / `ChatGoogleGenerativeAI` plus the model id). A step that walked
four rate-limited accounts shows one failed provider run per attempt before the
one that succeeded.

The one thing that would otherwise be missing — the *reason* a step rerouted —
is attached explicitly by the router's failure handler
([Making a reroute visible](../pool/failover.md#making-a-reroute-visible)).

## The local trace

Set `EVAL_TRACE_FILE` and the agent appends one JSON object per LLM/tool event:

```bash
EVAL_TRACE_FILE=run.jsonl python -m agent.code workdir < brief.md
```

Unset, nothing is attached and the agent behaves exactly as before. Zero cost
when off.

Event shape:

```
{ts, event, model, provider, tokens_in, tokens_out, text, tool, ok, detail}
```

Two producers write to one file: a LangChain callback handler
([trace.py](../../agent/utils/trace.py)) for LLM start/end and tool start/end, and the
router's own logging for routing and failover lines as structured records
rather than prose.

The handler is registered as an **inheritable callback on the run config**, not
on the model. That detail is what makes failover countable: it records the
*provider* models the router delegates to, not just the wrapper. A rerouted
attempt appears as an `llm_error` followed by another `llm_start` on a different
model — which is precisely how `failover_bounces` and `models_used` are derived.

**That inheritance comes from LangGraph, not from the router.** The provider
call is made with no explicit config and relies on an ambient run context to
nest under. A caller driving `RouterChatModel` directly — no graph, so no
ambient context — silently gets wrapper events only, and every provider-level
metric reads zero. Such callers must pass their config via the model's
`provider_config` field; it defaults to `None`, which is exactly the
inherit-from-the-graph behaviour a LangGraph caller depends on.

`llm_end` carries `text`, the model's reply, clipped like everything else.
It is the per-*attempt* view, so a malformed reply is attributable to the pool
member that produced it — and it is the only place a reply survives before the
envelope parser has run, which is what separates "the model wrote nonsense"
from "the parser mangled sense".

Tool args, outputs and reply text are clipped to 2 KB.

## The shape is a contract

Every automatic metric is a count or a sum over this file — no log scraping with
regexes, no dependency on a hosted service. That makes the event shape an
interface: [tests/agent/test_trace.py](../../tests/agent/test_trace.py) exists to
pin it, because a silent change to a field name would break metrics
retroactively across every recorded run.

## Reading routing decisions live

At `INFO`, the `LLMRouter` logger narrates each decision:

```
Routing to Llama3_70b_groq_1 (model=llama-3.3-70b-versatile, ~4200 tok).
Llama3_70b_groq_1 transient failure; rerouting. code=rate_limit_exceeded; ...
Llama3_70b_groq_1 exhausted/failed. Entering cooldown for 30s.
Whole pool in cooldown; waiting 47s for the next account.
Llama3_70b_groq_1 has finished its cooldown and is available again.
```

Everything else is quieted deliberately, because these five lines are the ones
that explain a run's behaviour.

## The record: one run tree

The narrow-role arm wrote four files about itself, three of which recorded
handoffs *between roles*. A conversation has no handoffs, so when that arm was
deleted ([The arm that was deleted](../agents/code.md#the-arm-that-was-deleted)) the only record left
was a flat event log that a reader has to re-assemble into the tree it came
from.

The JSONL in [The local trace](#the-local-trace) is still written, and still has to be:
every automatic metric is a sum over it ([Metrics](metrics.md)), and it
lands on disk whether or not LangSmith is reachable. What it does not do is
*read* well. So the two coexist and answer different questions — the flat file
is what gets counted, the tree below is what gets read.

So that arm records the tree instead, at
[agent/utils/run_tree.py](../../agent/utils/run_tree.py): one nested JSON object per run,
built from the tree LangSmith already assembled and then condensed down to the
turns, the tool calls and what each one cost ([What goes to disk: the condensed run](#what-goes-to-disk-the-condensed-run)).

```bash
AGENT_TRACE_FILE=run-tree.json python -m agent.code workdir < brief.md
```

**Ask for the trace, not for a run.** `collect_runs()` learns locally which runs
happened, so no network call is needed during the run. But it appends in
**completion order**, so its first entry is the innermost LLM call — and under
the old v1 fetch, asking LangSmith for a leaf returned a perfectly valid
*one-span* tree that looked like a working trace until you counted the spans. It
was recorded that way twice before the count was checked. Every run carries the
root's id as `trace_id`, and the v2 endpoint is keyed on exactly that, so the
question is now unaskable: any span the collector saw names the whole trace.

**The v1 run endpoints are gone on 2027-01-31.** `Client.read_run` (`GET
/runs/{run_id}`) and the `load_child_runs=True` flag behind it (`POST
/runs/query`) are replaced by `Client.traces.list_runs`, which is why
`requirements.txt` pins `langsmith>=0.11.1` — the `traces` resource does not
exist before it. Two consequences worth knowing when reading the code: the
project id is now a **required** argument, taken off the collector's `RunTree`
as `session_id` so the common path still costs no extra round trip; and the
response is **flat**, so the nesting is rebuilt locally from each run's
`parent_run_ids`. A span whose parent is missing is re-attached to the root
rather than dropped, because silently losing a span is the failure this whole
file exists to avoid.

**Everything the vendor returns is fetched** — which under v2 means asking for
it, since the endpoint returns bare ids unless the fields are named, so the
fetch names all of them. What is *written* is smaller, and deliberately: see
[What goes to disk: the condensed run](#what-goes-to-disk-the-condensed-run).

**Children are sorted by `start_time`.** `traces.list_runs` returns the batch
newest-first, so appending in arrival order built every tree backwards — turn
17 first, turn 1 last. That reads as a plausible run right up until you notice
the context shrinking from one turn to the next instead of growing.

**It needs `LANGSMITH_TRACING=1` and a key.** Without them the run is unaffected
and the record says `"run": null` — which is the right failure, because losing
a record is bad and losing the *run* because recording it failed is worse.

## What goes to disk: the condensed run

The fetched tree is faithful and unreadable. The first real one measured **22 MB
across 255 spans** for a 17-turn run, and **88% of that was `inputs`** — because
every level of the six-deep middleware tower carries its own copy of the whole
message history, and the history is replayed in full on every turn. The
conversation underneath is a few dozen KB. The rest is the same text written
back down a hundred-odd times.

So `condense()` keeps the run and drops the recording apparatus. Three moves:

| Move | What goes | Why it is safe |
| --- | --- | --- |
| **Only spans that did something survive** | the 195 `chain` spans — middleware wrappers, the graph's `model` and `tools` nodes | their inputs and outputs are their child's; an `llm` span is a turn and a `tool` span is a tool call, and between them they hold everything |
| **Tools group under the turn that asked for them**, matched by `tool_call_id` | the graph's sibling arrangement, where a tool call hangs off the root next to the model call rather than under it | a turn becomes what it actually is: the model spoke, then these tools ran and returned this |
| **One copy of the history per call, not eight** | the same messages recopied at every level of the middleware tower | the tower's copies are all the same list; the call's own `input` is the one that matters |

Also lifted out and written once: the **system prompt** and the **task**, which
are constant and large; and the **tool schemas**, of which only the names
survive — hundreds of KB repeated on every model span, and the schemas are in
the code. Inside each turn's `input` the system prompt is stood in for by a
marker rather than repeated, because 15 KB × 17 turns is a quarter of a
megabyte of the same text. A system message that *differs* from the hoisted one
is written in full, because then it is news.

The result on that same run is **248 KB, 1.1% of the original**, with nothing a
turn did truncated: prompts, replies, tool arguments, tool output and errors are
kept whole. It is the duplication that goes, not the content.

### The shape of a turn

Each turn answers "what entered the model here, and what came back":

| Field | What it holds |
| --- | --- |
| `input` | **the whole conversation as that call received it** — every message in order, each with its role, text, `tool_call_id` and the tool calls it carried |
| `output` | what the model returned: `text` when it spoke, `tool_calls` with their arguments, `finish_reason`, and `error` if the call failed |
| `tool_results` | what running those calls produced — linked back by `tool_call_id`, with status, duration and full output. Kept apart from `output` because these did not come out of the model, and without repeating the arguments already recorded there |
| `model`, `provider`, `tokens`, `seconds` | which member of the pool served this turn and what it cost |

Storing the history per call is redundant on purpose — turn N's `input` is
mostly turn N-1's — and it is the redundancy worth paying for, because the
question this file exists to answer is *what did the model actually see at the
moment it went wrong*. At 17 turns it costs about 200 KB. The duplication that
was removed was the other kind: the same list copied eight times **within a
single turn** by the middleware tower, which answers nothing.

### What the condense refuses to lose

Two things are worth spending bytes on, because nothing else in the record holds
them:

- **`attempts`** — the provider calls the router made for one turn. Omitted when
  a single attempt succeeded, since that only restates the turn; present the
  moment the pool had to work for the answer. This is the failover
  ([Failover](../pool/failover.md)) and it is invisible in every other artefact.
  `run.provider_failures` counts them.
- **`context_rewritten`** — a flag on any turn whose history is not the previous
  turn's extended, which from outside is what summarization looks like. `input`
  holds what the model saw either way; the flag is what says *this* turn is
  where to look, without diffing seventeen histories to find it. `context_messages`
  is the cheap version to scan: read down the column, it should climb, and a
  drop dates the summarization. A post-mortem asking "was that fact still in the
  context?" ([9](../design/long-run-harness.md#9-reading-one-session-back-the-post-mortem))
  is asking about precisely this event.

The header also keeps `langsmith_url`, so the untouched tree is one click away
until it expires — which is the whole reason a local snapshot exists
([Why two](#why-two)), and the reason the condense can afford to be aggressive.

**The root span is usually still `pending` when the fetch runs.** It closes last
and the tracer flushes asynchronously, so its own end time and latency are
typically absent; the wall time is taken from the last span to finish instead.
For the same reason the final turn's text is sometimes missing from the tree —
`stdout.log` holds the agent's closing message, and is the place to read it.
