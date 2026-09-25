[← Wiki index](../README.md)

# Changing how an agent behaves

*The working loop for any change meant to make an agent act differently: find
the decision a run got wrong, freeze it as an example, change the agent until
the example passes, and keep what already worked working. The probes
([Probes](probes.md)) are the instrument; this page is the method.*

## The loop

1. **Run a task a user would ask for.** Scenarios first
   ([Evaluation method](method.md)). Later, other sources of real tasks: recorded
   sessions, delegations, the explorer's own requests.
2. **Find the turn it went wrong.** Read the run tree turn by turn. The
   `trace-reviewer` agent does this for one run
   ([.claude/agents/trace-reviewer.md](../../.claude/agents/trace-reviewer.md)),
   and the J2 batch analysis ranks which of many to start from. Both read runs
   the way [Reading a recorded run](reading-runs.md) sets out.
   What you want is the **earliest decision that changed the outcome**, not
   the last symptom. In the explorer's Claude Mods run, the symptom was a
   wrong overview. The decisions were a leading brief (turn 5), searches that
   could not succeed (turn 7), a page that claimed evidence it did not have
   (turn 10), and a review that signed it off (turn 44).
3. **Freeze the situation as an example.**
   `python -m evals probes --from-run <record> --turn N --agent A --id <name>`
   prints a probe whose prompt and history are the run's own conversation up to
   that turn. Write two things by hand:
   - `must_not`: the move that went wrong, named, with the run's own words
     for why. Add any other move you would call a failure at that point.
   - `options`: the moves that make sense there, each with a `because`
     ([The expectations](probes.md#the-expectations-options-and-must_not)).
     Listing them is deciding how the agent should work at that point, so
     it belongs in the diff a reviewer reads. A move you did not think of
     scores `unlisted`, which is the prompt to put it on one of the two lists.
   - `why`: the run, the turn, and what went wrong there.

   Put the probe in its topic's file ([Datasets](#datasets-one-topic-each)) and commit it before any fix.
4. **Show it is red.** Score the recorded decision against it: a probe that
   passes the decision that went wrong asserts the wrong thing. Then run a
   baseline experiment with three or more repetitions. A probe that passes at
   baseline does not reproduce the failure. Rework it or drop it.
5. **Change the agent until it passes.** Iterate with experiments on the whole
   dataset ([Reading an experiment](#reading-an-experiment)). Reach for the levers in this order, the one the
   [deepagents skill](../../.claude/skills/deepagents/SKILL.md) sets out:
   1. the prompt or a tool description;
   2. what tools are offered;
   3. which models serve the agent;
   4. middleware;
   5. structure, such as a sub-agent that works in a fresh context.
6. **Accept.** The failure examples pass in most repetitions, and the
   regression examples in the same dataset do not drop. A change to behaviour
   still goes through a scenario run before it merges
   ([The promotion rule](method.md#the-promotion-rule)). Probes say the agent
   decides better at one point; only a scenario says the task gets done.

## Reading an experiment

- **One experiment runs a whole dataset**, so the failures a change targets and
  the regressions beside them are measured together. Name each side
  (`--name explore-baseline`, `--name explore-fix`) and compare them in
  LangSmith.
- **Three repetitions is the floor, and still small.** A probe is one sample
  from a pool whose members change between calls. At three per side, a
  difference of one or two passes is noise; a move from 0/3 to 3/3 is not.
- **Run the baseline in the same session as the fix.** Which pool members
  are awake changes by the hour.
- **Fixing a probe's pattern is not a new experiment.** When an expectation
  turns out to be wrong (a regex that caught "community terminology"), rescore
  the stored outputs of both sides with the corrected probe; don't rerun them.
- **Check who decided.** Every failure in the explorer's baseline came from a
  full flash model, none from flash-lite. That ruled out "the weak model did
  it" before a single prompt was edited.

## Examples of what already works

Most examples come from failures. A few must come from decisions the agent
already gets right: `kind: regression`. Without them, a fix for one failure is
free to break the normal path, and nothing notices.

Take them from recorded runs, the same way, preferably the same runs. The
Claude Mods run went wrong at the review but did two things right: it looked at
the wiki before delegating (turn 1), and a researcher that found real
documentation cited it (turn 19). Both are regression examples now. Keep them
fewer than the failures, roughly one for every three. They exist to catch
collateral damage, not to measure progress.

## Datasets: one topic each

One probe file is one topic is one LangSmith dataset. It names its `dataset`,
so the grouping is reviewed in the same diff as the probes.

A topic is **one agent's family of decisions that one change would plausibly
move together**. That is what makes "the regressions in the same dataset" the
right regression check.

- Aim for 5–20 examples per dataset.
- Split one above ~25, or when two groups inside it never move together.
- Fold a file under ~3 into its neighbour.
- A dataset per agent is too generic: the coding agent's editing and its
  delegation fail for unrelated reasons. A dataset per failure is too many:
  nothing would carry regressions.

| Dataset | Topic |
| --- | --- |
| `probes-code-editing` | How the coding agent changes a repository: reading first and not twice, the shell and workspace it has (no git where there is no repository), recovering from a refused call |
| `probes-code-scope` | What the task allows the coding agent to change: a stale page the task's own source overrides, against a guarantee someone else relies on (the Contradicted Requests section) |
| `probes-improve-diagnosis` | How the improvement agent diagnoses: the ledger before the traces, a diagnosis before any delegation or edit, and no issue, delegation or report the evidence it already read contradicts |
| `probes-explore-evidence` | What the explorer's pages claim and on what evidence: searching, sourcing, figures no page carries, claims that something does not exist |

## Reviewing examples

An example freezes a situation, a conversation that reached a decision. Once the
agent changes, it may never reach that point again. A renamed tool, a new
step, or a different first move all leave the probe testing a path nobody takes,
and it keeps reporting a result that looks meaningful.

So every probe carries `reviewed: YYYY-MM-DD`, and
`python -m evals probes --stale` lists the probes whose agent has changed since
their review. "Changed" means the agent's package, the shared prompts and tool
surface, or the pool's model config. Router internals are left out: they
change who answers when, not what the agent is shown.

To review a probe, check that its situation still occurs:

- Does a recent run still reach that point?
- Do the tools and steps in its history still exist?
- Would the agent still be asked that question in that form?

If yes, bump `reviewed`. If the path has moved, regenerate the probe from a
fresh run with `--from-run`. If the situation can no longer occur, retire the
probe. Never patch a history by hand to fit a new agent: that turns a recorded
situation into an invented one.

## The first case: the explorer's absence claims

*Open. The record so far, on branches `explore-nonexistence-claims` and
`probes-expansion`.*

The explorer concluded that Claude Mods do not exist; they do
([research/claude-mods/postmortem.md](../../research/claude-mods/postmortem.md),
kept locally). Five failure examples came from that run. Against the current
prompts they score 3/15; two rounds of prompt and tool-description changes
scored 5/15 and 4/15. The one change that clearly helped was telling the
researcher that `site:` and `OR` are not applied: that probe went from 0/3 to
2/3 in both rounds. The absence claims did not move, including on full flash
models.

The reading: these examples start after the conversation has committed to the
wrong answer. The brief asked for the alias explanation, and the researcher's
own reflection already said "no official feature". Rules in a long prompt do not
undo that.

**The dataset outgrew one run (2026-09-20).** Seven probes were added from the
2026-09-10 machintl market-analysis runs, where the same family fails without
any absence claim: a researcher ruling gesture analysis lawful and minimal-risk
with no page saying so, quoting a price the one page it read calls unpublished,
and writing margins and a payback that first appear in its own `think_tool`
reflection. Four more candidates were screened out because they passed 3/3 at
baseline. Every one of these claims enters through a reflection, which the tool
echoes back as a `ToolMessage` — the model's memory arrives in the same shape
as a page.

**Structure was the lever that moved, and only where it applies.** Pinned to
`gemini-3.5-flash-lite`, three repetitions:

| Experiment | Failures | Regressions |
| --- | --- | --- |
| `explore-evidence-baseline2-lite` | 9/30 | 8/12 |
| `explore-evidence-fix1-lite` (researcher prose: reflections are not sources) | 6/30 | 9/12 |
| `explore-evidence-fix2-lite` (`review-agent`, the check in a fresh context) | 11/30 | 11/12 |
| `explore-evidence-fix3-lite` (both) | 12/30 | 8/12 |

`fix2` is what merged ([The check that did not do the research](../agents/explore.md#the-check-that-did-not-do-the-research)).
It moves exactly the two orchestrator probes that sign off and integrate an
unsupported absence claim, 1/3 and 0/3 to 3/3 each, and it moves them by
changing what the orchestrator does at that point: it delegates the check
instead of writing. **That is a path those probes were frozen against, and they
now test the delegation rather than the claim** — said here rather than patched
into the histories. Whether the reviewer catches the claim is not measured by a
probe: driven once by hand over the Mods page, flash-lite searched for plugins
rather than for "Mods" and confirmed the false claim. A third prose round on
the researcher did not move the researcher's own probes and is not kept.

What is still open: a pinned full-flash pair (the machintl failures were
`gemini-3.5`/`3.6-flash`, and both models' daily quota was spent), a probe on
the reviewer itself, and the scenario run the promotion rule asks for.
