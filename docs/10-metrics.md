[← Wiki index](README.md)

# 10. Metrics

*What gets measured, what each number is for, and why they're never collapsed
into one score.*

## 10.1 The axes

Metrics are grouped by the question they answer. Keeping the grouping explicit
is what stops a comparison from being read as "number went up".

| Axis | Metrics | What it's for |
| --- | --- | --- |
| **Task success** | `outcome`, `f2p_ratio`, `p2p_ratio` | The only gating axis. |
| **Diagnosis** | `failure_class` | What to actually work on next. |
| **Autonomy** | `ran_own_tests`, `self_corrected` | Closing its own loop is the difference between an agent and a code generator. |
| **Efficiency** | `provider_calls`, `failover_bounces`, `tokens_*` | On a free pool this *is* the cost model. A run that passes but drains the pool is a weak pass. |
| **Robustness to failover** | `models_used` vs `outcome` | Did switching model mid-task derail it? |
| **Integrity** | `tampered` | Non-negotiable, tracked separately so it can never be averaged away. |
| **Quality** | Judge scores | Right for the right reason, and in the right scope. |

## 10.2 Automatic metrics

Every one is a count or sum over `trace.jsonl`, the diff, and the verification
output ([7.3](07-observability.md#73-the-local-trace)).

| Metric | Definition |
| --- | --- |
| `outcome` | `pass` \| `fail` \| `crash` \| `timeout` \| `tampered` |
| `f2p_ratio` | `fail_to_pass` tests flipped / total — partial credit on the fix |
| `p2p_ratio` | `pass_to_pass` tests still passing — collateral damage |
| `failure_class` | `retrieval` \| `tooling` \| `reasoning` \| `stopping` — see below |
| `steps` | Agent loop iterations (tool-call rounds) |
| `provider_calls` | LLM calls, including failover retries |
| `failover_bounces` | Transient failures before a step succeeded — wasted quota |
| `tokens_in` / `tokens_out` | Summed where the provider reports usage |
| `bad_tool_calls` | Invalid tool name, failed `edit_file`, malformed args |
| `models_used` | Distinct models that served a step, and the per-model call mix |
| `ran_own_tests` | Did the agent invoke `execute` on the test command itself |
| `self_corrected` | Did a failing `execute` get followed by another edit |
| `files_touched` / `diff_lines` | Change size, vs the reference solution's size |
| `wall_time_s` | End to end |

## 10.3 Failure taxonomy

**This is the highest-leverage metric in the set.** A pass rate tells you a
configuration is worse; it doesn't tell you what to fix. Since we know which
files the reference solution touches, every failed run can be classified
automatically from the trace — no transcript reading.

| Class | Signal | What it means | What fixes it |
| --- | --- | --- | --- |
| `retrieval` | Never read a file the gold patch touches | Couldn't find the code | Better search/navigation tools, or context injection |
| `tooling` | Read it, but `edit_file` calls failed to apply, or `tool_use_failed` | Knew what to change, couldn't express the edit | Change the edit format — line-anchored or whole-file rewrite instead of exact-string match |
| `reasoning` | Edits applied cleanly, tests still fail | Wrong fix | Stronger model tier; better prompt |
| `stopping` | Hit the recursion limit, or declared done without running tests | Loop problem | Step budget, prompt, forcing a self-test before finishing |

**Why it matters most here specifically.** On small models the `tooling` class
is likely to dominate. Groq already returns `tool_use_failed` carrying the
model's raw malformed output, which the router surfaces today
([4.7](04-failover.md#47-making-a-reroute-visible)). If the taxonomy shows a
large share of failures are malformed edits rather than wrong reasoning, then
**changing the edit tool's format is a bigger win than any model or prompt
change** — and that is not a conclusion you would reach by staring at a pass
rate.

It also answers the "bad retrieval vs. bad editing" question that normally
requires trajectory inspection — cheaply, for every run.

## 10.4 The judge

`claude -p` against a fixed rubric. The judge sees: the task prompt,
`diff.patch`, the verification output, `evaluation/solution.patch`, and
`evaluation/criteria.md`. It returns JSON scoring 0–4 on:

- **correctness beyond tests** — right for the right reason, or coincidence?
- **scope discipline** — did it change only what the task asked for?
- **code quality** — does it read like the surrounding code? (the `ponytail` bar)
- **instruction adherence** — did it follow explicit constraints in the brief?

Three rules make scores comparable across time:

1. **Blind** — the judge is not told which configuration produced the diff, and
   runs are shuffled before judging.
2. **Pinned** — the judge model id and rubric prompt version are recorded in
   every verdict. Changing either starts a **new comparison epoch**; you cannot
   compare judged scores across epochs, only re-judge.
3. **Diff-addressed** — verdicts are cached by diff hash, so identical outputs
   score identically and repeat judging is free.

The judge is deliberately **not** shown the agent's transcript. The model that
writes a convincing narrative and the model that writes a correct patch are not
the same model, and we're grading the patch.

Status: not yet built ([11.2](11-eval-status.md#112-whats-built)). Quality
scoring is manual until then.

## 10.5 Ranking is lexicographic, not weighted

> success rate → integrity clean → judge quality → provider calls

Collapsing these into one number requires inventing weights, and the weights
would be doing the deciding. Keep the columns visible.

## 10.6 What a run leaves behind

```
evals/results/runs/<run_id>/
  run.json         fingerprint, scenario tag, metrics, outcome
  trace.jsonl      captured events
  stdout.log
  stderr.log
  diff.patch       untouched code → final workdir
  verify.txt       hidden-test output
  judge.json       rubric scores + judge model/prompt version
```

Each directory is self-contained and carries every field a leaderboard needs, so
a summary is a glob rather than a query against a shared file — and merging a
configuration branch can never conflict over results
([8.3](08-evaluation-method.md#83-where-things-live)).

It also keeps the *evidence* behind each row, which is the part hosted tracing
loses. It is gitignored — an artifact of a run, kept on the machine that made
it. What goes in the repo is the writing over it: conclusions in
`evals/results/reports/<date>-<topic>.md`, citing runs by id, and the one-line
verdict in the ledger, [evals/CONFIGS.md](../evals/CONFIGS.md).

---

**Previous:** [← 9. Scenarios](09-scenarios.md) · **Next:** [11. Evaluation status →](11-eval-status.md)
