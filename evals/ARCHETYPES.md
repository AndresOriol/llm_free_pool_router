# Request archetypes, and the scenarios drawn from them

`python -m evals mine --out <dir>` turns the recorded Claude Code sessions into
a corpus. This file is what was found in it, and it is the half that matters:
the extractor interprets nothing, so the judgement about what a session was
worth lives here.

Seven shapes recur across the corpus. Five became scenarios; two did not, and
the reason is recorded so nobody re-derives it.

Read [Scenarios](../docs/evaluation/scenarios.md) for what a scenario has to be, and
[design/generative-scenarios.md](../docs/design/generative-scenarios.md) §2 and
§4 for why a recorded request is better raw material than an invented one.

## Why the corpus is the right source

A scenario invented to be testable tests what is easy to grade. A scenario
recovered from a real request tests what someone actually needed, and the
oracle has to be built to fit it rather than the other way round. That is the
transferable idea from CursorBench — *the prompt that produced this commit* is
a scenario factory — and until the corpus existed it was a roadmap item.

What survives in a session and makes this work: the human turns in order, every
tool call with its arguments, which files were written and how often, the shell
commands run, the git branch, and the timestamps.

## The seven

| # | Archetype | What the request looks like | Scenario |
| --- | --- | --- | --- |
| 1 | **Propagate a spec change** | a document changed; make the code agree, and find every place it touches | `scenario/pipeline/model-v3-propagation` |
| 2 | **Refactor to a shape** | "make these share a base class" — where the shape flattens a difference only prose explains | `scenario/bots/bots-to-base-class` |
| 3 | **Build to a format spec** | write the thing that produces this output; a consumer rejects the file on the first bad line | `scenario/export/stock-export` |
| 4 | **Cover what is untested** | the suite passes and proves almost nothing; make it catch the failures that matter | `scenario/suite/cover-the-rejections` |
| 5 | **Decide an ambiguous word** | one word with two readings the tree supports, and nobody awake to ask | `scenario/usage/which-accounts-are-active` |
| 6 | **Fix the reported case, then find its siblings** | a bug report names one input; the same defect has three other spellings | **unbuilt** — see below |
| 7 | **Resume someone else's half-finished work** | a branch with a broken middle and no account of what was intended | **unbuilt** — needs `NOTES.md` fidelity the set does not have yet |

## 6, the strongest unbuilt one

It recurs more than any other shape in the corpus and nothing in the set probes
it. The request names one failing input; a competent fix generalises, and a
merely responsive one patches the reported case and leaves the siblings.

It is worth building because it splits two behaviours the current set scores
identically: an agent that fixes exactly what was asked, and one that reads the
rule behind the request. `fail_to_pass` would carry the reported case, and the
siblings would be hidden tests the visible suite never hints at.

The reason it is unbuilt is authoring cost, not doubt: the sibling cases have to
be genuinely discoverable from the code rather than guessable from the prompt,
or the scenario measures luck.

## 7, and what it needs first

A session that resumes another's work is only gradeable if the account it reads
is load-bearing, and
[The account, and why it is only counted](../docs/evaluation/metrics.md#the-account-and-why-it-is-only-counted)
records that 7 of 23 successful runs wrote one at all. Build the expectation
first, then the scenario that depends on it.

## What this is not

The extractor does not label archetypes and should not learn to. Labelling is
the authoring judgement, it is wrong often enough to matter, and a table a human
maintains is cheaper to correct than a heuristic a human has to argue with.
