---
name: behaviour-change
description: The agent-improvement loop, test-first - from recorded runs to the turn one went wrong, a probe frozen there, a change until it passes in LangSmith experiments, and the dataset's regression examples kept green. Start here for any agent improvement - it routes to trace-reviewer (one run), j2-error-analysis (a batch) and deepagents (making the change). Use when asked to improve or fix an agent's behaviour, make an agent stop/start doing something, act on a trace review or J2 finding, add probes or dataset examples, or review stale probes.
---

# Changing how an agent behaves

The method is [Changing how an agent behaves](../../../docs/evaluation/changing-behaviour.md);
the instrument is [Probes](../../../docs/evaluation/probes.md). Read the method first.
This file is the order of work and the commands. It is the entry point for
improving an agent, and the pieces below are its stages:

| You have | Use | It gives you |
| --- | --- | --- |
| A batch of runs, and no idea yet what to fix | `j2-error-analysis` skill | ranked failure categories, each naming a run and turn to probe |
| One run that went wrong | `trace-reviewer` agent | the turn it could not recover from, and the probe command |
| A turn to freeze | §2 below | a probe, red at baseline |
| A red probe | `deepagents` skill, §4 below | the change, on the lightest rung that works |

How to read a run, and the traps in doing so, are in
[Reading a recorded run](../../../docs/evaluation/reading-runs.md), the one
copy of those rules.

Behaviour is judged by **experiments**, not unit tests. `pytest` covers the probe
machinery. It says nothing about whether the agent decides well.

## 0. Before anything

```bash
python -m evals probes --stale      # probes due for review; review them first
python -m evals probes --list       # what exists, by dataset
```

A stale probe in the dataset you are about to change makes that dataset's
numbers meaningless. Review it (docs/evaluation/changing-behaviour.md#reviewing-examples) before relying on them.

## 1. Find the turn

Start from a recorded run of a task a user would ask for: a scenario run under
`evals/results/runs/`, or any run record (`AGENT_TRACE_FILE`). Coming from J2,
start from the run and turn its top recommendation names. Use the
`trace-reviewer` agent for one run, or read the record's `turns` yourself.

Name the **earliest decision that changed the outcome**, with its turn `n` and
the model that made it. Several decisions can each be a probe. A probe needs
the run's `trace.json`. A run without one can only be diagnosed, not frozen, so
record a traced run of the same scenario first.

## 2. Freeze it

```bash
python -m evals probes --from-run <run_dir>/trace.json --turn N --agent <code|improve|explore|explore-researcher> --id <what-it-must-not-do>
```

This prints a skeleton. Fill in:

- `why`: the run, the turn, and what went wrong, quoted from the record.
- `expect`: prefer the negative (`not_tool`, `not_tool_with_args`,
  `text_not_matches`). Name the move that was wrong. Don't try to list every
  right one.
- `through`: side-effect-free tools (`think_tool`, `research_status`,
  `read_file`, `write_todos`) when the decision under test comes after a
  reflection.

Put it in the topic file it belongs to (docs/evaluation/changing-behaviour.md#datasets-one-topic-each). If none fits, start a
new file with its own `dataset:`, but only for a genuinely separate family of
decisions. Add a `kind: regression` example for a decision the same run got
right: about one for every three failures.

## 3. Show it red

1. Score the recorded decision offline against the new probe with
   `probes.score(probe, {"tools": [...recorded calls...], "text": "", "error": ""})`.
   It must fail. If it passes, the expectation is wrong.
2. Push, then run the baseline on the **unchanged** agent:

   ```bash
   python -m evals probes --dataset <ds> --push
   python -m evals probes --dataset <ds> --experiment --name <topic>-baseline --repetitions 3
   ```

   A failure probe that passes 3/3 at baseline does not reproduce the failure.
   Rework it or drop it.
3. Commit the probes on their own: `Probe <behaviour>`.

**Do not edit the agent while an experiment is running**: the agent is built
from its files when each probe starts.

## 4. Change the agent

Apply the `deepagents` skill first. Its extension ladder is the one ordering
of levers (prose → skill → harness profile → model selection → tool →
middleware → sub-agent → backend). Go down a rung only when the one above has
been tried and measured. For a judgement failure, "a stronger model" is a
legitimate answer. A sub-agent is not
([Judgement, not topology](../../../docs/evaluation/reading-runs.md#judgement-not-topology)).

After each change:

```bash
python -m evals probes --dataset <ds> --experiment --name <topic>-fix<N> --repetitions 3
```

Compare against the baseline, per probe and by the model that decided. If a
probe's expectation turns out wrong, fix it and **rescore the stored outputs of
every side** rather than rerunning. Two rounds of one lever that move nothing
mean it is time for the next rung. Report the numbers to the user before
reaching for it.

## 5. Accept

- The failure probes pass in most repetitions.
- The dataset's regression probes have not dropped.
- `python -m pytest tests/evals` passes.
- A change that alters behaviour still goes to a scenario run before it merges
  (docs/evaluation/method.md#the-promotion-rule).

Commit the change separately from the probes, with the experiment names in the
body. Bump `reviewed` on every probe whose situation you re-checked.
