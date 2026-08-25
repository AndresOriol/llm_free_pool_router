[← Wiki index](README.md)

# 7. Observability

*Two traces, deliberately. One to watch a run, one to score it later.*

## 7.1 Why two

They answer different questions and have different lifetimes.

| | LangSmith | Local JSONL trace |
| --- | --- | --- |
| Answers | *What is this run doing right now?* | *What exactly happened in this run, forever?* |
| Lifetime | Expires | On disk, for as long as the run directory is kept |
| Cost | A hosted dependency | None |
| Used by | A human eyeballing a live run | Every automatic metric in [10. Metrics](10-metrics.md) |

The rule that follows: **LangSmith is for watching, never for the record.** A
verdict must rest entirely on files on disk. This is not a preference — hosted
traces expiring would silently invalidate old comparisons, which is exactly the
failure mode an evaluation system exists to prevent.

## 7.2 LangSmith

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
([4.7](04-failover.md#47-making-a-reroute-visible)).

## 7.3 The local trace

Set `EVAL_TRACE_FILE` and the agent appends one JSON object per LLM/tool event:

```bash
EVAL_TRACE_FILE=run.jsonl python -m agent.harness workdir < brief.md
```

Unset, nothing is attached and the agent behaves exactly as before. Zero cost
when off.

Event shape:

```
{ts, event, model, provider, tokens_in, tokens_out, text, tool, ok, detail}
```

Two producers write to one file: a LangChain callback handler
([trace.py](../agent/runtime/trace.py)) for LLM start/end and tool start/end, and the
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

## 7.4 The shape is a contract

Every automatic metric is a count or a sum over this file — no log scraping with
regexes, no dependency on a hosted service. That makes the event shape an
interface: [tests/agent/test_trace.py](../tests/agent/test_trace.py) exists to
pin it, because a silent change to a field name would break metrics
retroactively across every recorded run.

## 7.5 Reading routing decisions live

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

## 7.6 What a session records about itself

The trace answers *what happened*. A session harness run also has to answer
*what each role was given*, and that is a different file.

| Under the workdir's `.harness/` | One line/file per | Holds |
| --- | --- | --- |
| `journal.jsonl` | completed step | The brief (`goal`, `context`, `done_when`), the resulting `status` and `finding`, and the commands run with their exit codes |
| `steps/NN-<role>.md` | model turn | The brief, the prompt as rendered, the raw reply before parsing, and the tool calls with their outputs |
| `reports/session-*.md` | session | The rationale, built from the journal — the artefact a human reviews instead of the code |

The split is a cost decision. The journal is the crash-resume substrate
([6.12](06-agent.md#61-what-it-is)),
re-read line by line every time a killed session resumes, so the bulk stays out
of it. The transcript runs to a few kilobytes per turn and is only opened when a
step needs explaining.

**Why the transcript exists.** A recorded run's journal said a writer was
blocked for want of the contents of a file, and nothing on disk said what the
writer had been handed. The orchestrator curating context downward is the whole
mechanism of the session design
([design note §8.1](design/long-run-harness.md#81-the-handoff-envelope)), and it
was the one variable not being logged. Naming a failure and diagnosing it are
different things, and only the second changes anything.

The eval runner copies all three into the run's results directory, before it
prunes the workdir. They are what the per-run post-mortem reads
([design note §9](design/long-run-harness.md#9-reading-one-session-back-the-post-mortem)).

---

**Previous:** [← 6. The coding agent](06-agent.md) · **Next:** [8. Evaluation method →](08-evaluation-method.md)
