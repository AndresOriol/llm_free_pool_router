[← Wiki index](../README.md)

# Reading a recorded run

*What to open in a run directory, and the traps that have already produced a
wrong conclusion here. The per-run post-mortem (`trace-reviewer`), the batch
analysis (J2) and a probe author ([Changing how an agent
behaves](changing-behaviour.md)) all read runs this way. This page is the one
copy of these rules.*

## What a run leaves

`evals/results/runs/<run_id>/`. Format details are in
[Observability](observability.md) and [Metrics](metrics.md#what-a-run-leaves-behind).

| File | Read it for |
| --- | --- |
| `run.json` | The verdict from the hidden tests (`outcome`, `failure_class`, `f2p_*`, `p2p_*`), `wall_time_s`, `config_sha`, and the automatic metrics |
| `trace.json` | The condensed run: header, `task`, `system_prompt`, then `turns`, each with the full `input` that call received. **The only source a probe can be frozen from.** ~250 KB for 17 turns, so read the header and the turn shape, then open single turns |
| `trace.jsonl` | The flat event log that every trace-derived metric is summed over |
| `stderr.log` | The router's narration: one timestamped `Routing to <account> (model=…, ~N tok)` line per provider call, plus reroutes, cooldowns and pool waits |
| `diff.patch` | What actually changed |
| `verify.txt` | The hidden tests' output. The summary is at the end |
| `stdout.log` | The agent's closing message and its todo list |

List the turns of a trace without loading all of it:

```bash
python -c "import json;d=json.load(open('trace.json'));print(json.dumps(d['run'],indent=2));[print(t['n'],t['model'],t['context_messages'],'REWRITTEN' if t.get('context_rewritten') else '',[r['name'] for r in t.get('tool_results',[])]) for t in d['turns']]"
```

A turn's `n` is the number `python -m evals probes --from-run <trace.json> --turn N`
takes. Cite turns by `n`, so a finding can be turned into a probe.

When there is no `trace.json`, rebuild the run from `stderr.log`. Read the
`~N tok` values as a sequence. Steady growth means history is building up. A
sawtooth means summarization fired. A flat line at a few thousand tokens means
the agent never built up any context.

## The traps

1. **The closing message is not evidence.** `stdout.log` is what the model said
   it did. Check it against `diff.patch` and `verify.txt`. A claimed change that
   the diff does not contain is the most important thing you can find
   ([The run that changed nothing and said otherwise](metrics.md#the-run-that-changed-nothing-and-said-otherwise)).
   A claim that traces to no `tool_results` or `tool_end` event is
   unverifiable. Say so; don't accept it.
2. **A zero is ambiguous.** `provider_calls`, `tokens_in`, `tokens_out`, `steps`,
   `tool_calls`, `models_used`, `ran_own_tests` and `self_corrected` come from
   `trace.jsonl`. Runs recorded before that file was written read zero whatever
   they did. Check that the file exists and is not empty before quoting any of
   them. When it is missing, count `Routing to` lines in `stderr.log` and say
   that you did. Reading such a zero as cheapness inverts the cost comparison
   this project depends on.
3. **`trace.json` is often absent, and that is not the run's fault.** It needs
   `LANGSMITH_TRACING=1` and is fetched *after* the work, so a run killed by
   the timeout never has one. Without it you can see which calls happened but
   not what they contained, and no probe can be frozen from the run.
4. **`stopping` is four failures under one name.** Separate them before you
   count:

   | What happened | How you tell | Where the fix is |
   | --- | --- | --- |
   | The loop never settled | `GraphRecursionError` in `stderr.log` | `RECURSION_LIMIT`, or the stop condition in the prompt |
   | Steady work ran out of clock | `Routing to` lines spread evenly up to the end | the scenario's `timeout_s`, or slow progress |
   | One provider call hung | a long silence between the last `Routing to` line and the timeout. One run made 2 calls in 2,722 s | not the agent: a request timeout, [awake.py](../../agent/utils/awake.py) |
   | The pool starved | `waiting …s for the next account`, or the floor check refusing | `CONTEXT_FLOOR`, [config.yaml](../../llm_router/config.yaml) |

   A run that hung has no trajectory to review. Say exactly that.
5. **A trace is older than the code.** A run exercised the commit in its
   `config_sha`. Before blaming a file, check that its last change is an ancestor
   of that commit (`git log -n 3 -- <file>`). A fix diagnosed from a run of old
   code re-does work that is already done
   ([What the first live pass showed](../agents/improve.md#what-the-first-live-pass-showed)).
6. **Pass rates at these sample sizes are noise, and cost replicates**
   ([The pass column is noise](../agents/code.md#the-pass-column-is-noise)).
   Read `tokens_in` first. Never rank on pass rate at n < 10.
7. **Old narrow-role runs are a different architecture.** `session`,
   `context-and-gate` and `harness-v*` runs carry `journal.jsonl`,
   `steps/NN-<role>.md` and `rationale.md` instead of a trace
   ([The arm that was deleted](../agents/code.md#the-arm-that-was-deleted)).
   Read them from those files, and never pool their counts with `code` runs.

## Judgement, not topology

If a run failed because the model decided badly, the honest fix may be "a
stronger model". A sub-agent, a role split or a routing rule addresses
retrieval, tooling and stopping, not judgement. This project has already made
that mistake once, and it comes back as "give it a sub-agent for that".
