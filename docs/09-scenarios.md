[← Wiki index](README.md)

# 9. Scenarios

*What a test case looks like, why the answers are hidden from the agent, and
how a scenario proves it still measures something.*

Scenarios live in the **`agent_evals`** repo, not this one
([8.3](08-evaluation-method.md#83-where-things-live)).

## 9.1 Storage: one branch per topic, one commit per scenario

```
topic/<topic>              the topic's line of scenarios
scenario/<topic>/<id>      tag on the commit that is that scenario
```

Grouping by topic keeps related codebase states on one line of history, so a
topic reads as a coherent body of test cases rather than a pile of unrelated
trees.

Tags are the handle runs actually use, and they are **never moved**. To change a
scenario, commit again and tag the new commit — so a result citing the old tag
stays reproducible forever.

## 9.2 Materialization is `git archive`, not a checkout

Three things follow, all load-bearing:

- **No `.git` reaches the workdir.** The agent cannot commit, cannot walk
  history to another scenario, and cannot read the withheld material.
- **Nothing in the scenario repo is ever written**, so no run needs reverting —
  deleting the temp directory is the entire cleanup.
- No detached-HEAD or dirty-tree handling on the scenario repo, and concurrent
  reads of different scenarios can't interfere.

## 9.3 Anatomy

```
<the code, at the root>   the code state — this is what the agent gets
tasks/<task-id>.md        one or more prompts posed against this code
evaluation/               WITHHELD: criteria.md, tests/, solution.patch
scenario.yaml             WITHHELD: metadata, test sets, immutable list
```

The code sits at the root rather than under a `seed/` wrapper, so a topic branch
checks out as a working codebase you can open and run. A scenario should look
like a real project, because that's what it stands in for.

**The withheld set is the whole game.** If the scoring tests ship in the
workdir, the agent can — and small models do — edit them until they pass, or
write code shaped to the test rather than to the requirement. They are overlaid
onto a *copy* of the finished workdir, so they apply even if the agent deleted
the visible ones. `scenario.yaml` is withheld too, because it names them.

The runner **asserts** the withholding after materializing rather than assuming
it. A scenario that leaked its own tests would score every configuration far too
well, and would read as a win rather than as a bug.

## 9.4 `scenario.yaml`

```yaml
id: router-cooldown
title: Cooldown timer never resets after a 429
category: bugfix            # bugfix | feature | refactor | tests | ambiguous | trap
difficulty: L1              # see the ladder below
tags: [python, single-file, long-context]
context_mode: none          # none | claude_md — does the code state ship a CLAUDE.md?
immutable:                  # files the agent must not modify; hashed pre/post
  - tests/test_cooldown.py
timeout_s: 900

fail_to_pass:               # must go from failing to passing — did it fix the thing
  - evaluation/tests/test_cooldown.py::test_cooldown_resets_after_retry_after
pass_to_pass:               # must stay passing — did it break anything else
  - evaluation/tests/test_cooldown.py::test_cooldown_blocks_while_hot
  - evaluation/tests/test_router.py
```

**Two test sets, not one command.** Borrowed from SWE-bench, and strictly a
better acceptance contract: "the bug is fixed" and "nothing else broke" are
different claims, and a small model that deletes an inconvenient branch
satisfies the first while failing the second. A run passes only if every
`fail_to_pass` flips **and** every `pass_to_pass` holds.

**`context_mode`** mirrors agentbench's context settings. Since the agent loads
a workdir's `CLAUDE.md` as its system prompt
([6.3](06-agent.md#63-the-agents-instructions)), running the same scenario in
both modes measures how much the harness depends on curated context — worth
knowing before investing in more of it.

## 9.5 The task file

```markdown
---
id: fix-from-failing-test
suite: [smoke, full]
tags: [bugfix, test-driven]
---

## Prompt
<the exact text piped to the agent's stdin>

## Acceptance
Inherits the scenario's fail_to_pass / pass_to_pass, unless overridden here.

## Judge notes
What a correct fix looks like; what counts as out of scope here.
```

One scenario, several tasks, is the point. The same buggy tree can pose *"here's
a failing test, fix it"*, *"users report X, find and fix it"* (no test given),
and *"add feature Y on top"* — three very different capability probes for one
authoring cost.

## 9.6 Categories to cover

| Category | What it probes |
| --- | --- |
| **bugfix, test-driven** | Baseline competence. Failing test provided. |
| **bugfix, symptom only** | Diagnosis — no test, the agent must localize. |
| **feature** | Multi-file construction, from a spec plus hidden tests. |
| **tests** | Writing tests for existing code; verified mutation-style (the tests must fail against a seeded broken variant). |
| **refactor** | Restraint — behaviour-preserving, hidden tests must still pass. |
| **long-context** | A large file that exceeds small-TPM pool members. Directly probes size-based routing ([4.2](04-failover.md#42-size-aware-selection)). |
| **ambiguous** | Judge-only. Does the agent ask, or invent requirements? |
| **trap** | The brief asks for something the code contradicts, or that would break a documented invariant. Measures over-eagerness. No auto-pass. |

## 9.7 The difficulty ladder

Scenarios carry an explicit difficulty, because **a task that every
configuration passes, or every configuration fails, carries no information.**
Public benchmarks are calibrated for frontier models; importing their difficulty
wholesale would produce a set where everything scores zero and no two
configurations are distinguishable.

| Level | Shape | Expected pass rate | Role |
| --- | --- | --- | --- |
| **L0** | One function, failing test handed over | ~100% | Canary — if L0 drops, the *harness* is broken, not the model |
| **L1** | One file, localize from a symptom | 50–90% | The main comparison instrument |
| **L2** | Multi-file, cross-module change | 10–50% | Comparison instrument as configurations improve |
| **L3** | Real SWE-bench Lite instances | ~0–5% initially | Ceiling probe, **not** a comparison instrument |

The `smoke` suite is drawn from wherever the current baseline sits between
roughly 20% and 80%. That band moves as the agent improves, so suite membership
is reviewed whenever a baseline is re-run: a task that saturates gets demoted to
regression duty and a harder one takes its place.

Recording L3 results is still worth it as an absolute-progress marker — just
never make a promotion decision on a metric that reads zero for both
configurations.

## 9.8 The validation gate

Every scenario must self-test before it's usable:

```bash
python -m evals validate
```

1. Untouched code + `evaluation/` → every `fail_to_pass` test **must fail** and
   every `pass_to_pass` test **must pass**. Otherwise the task is already
   solved, or the seed is broken in a way the task never mentioned.
2. Untouched code + `evaluation/solution.patch` → **all** must pass. Otherwise
   the task is impossible and every configuration scores a free fail.
3. Every file in `immutable:` exists in the code state.

This is SWE-bench's empty-patch / gold-patch gate, and it runs on every scenario
**every time the suite runs**, not just at authoring time. Dependency drift that
silently makes a `pass_to_pass` test fail on the seed would otherwise show up as
every configuration regressing at once — a conclusion that would waste days.

A scenario that fails validation never enters a suite. This single gate catches
most of the ways an eval set silently stops measuring anything.

---

**Previous:** [← 8. Evaluation method](08-evaluation-method.md) · **Next:** [10. Metrics →](10-metrics.md)
