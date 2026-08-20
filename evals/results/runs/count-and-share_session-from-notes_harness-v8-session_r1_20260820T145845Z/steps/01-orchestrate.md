# Turn 01 - orchestrate

_2026-08-20T14:58:54.347900+00:00_

## Prompt

```
# Task
Read NOTES.md and do what the newest feedback asks for. Check your work by
running `python -m pytest tests`, and update any documentation your change
makes wrong before you finish.

# Project notes
# Notes

Working notes for this project. I read this in the morning and leave feedback
under a dated heading; the agent reads the newest feedback, does the work, and
appends its own account below.

## Feedback — 2026-08-18

Finance flagged something odd on the monthly page: later summaries listed
categories from earlier months with a total of 0.00. Fixed — `summarise` was
accumulating categories in a module-level list instead of deriving them from
the entries it was given.

## Feedback — 2026-08-19

Two things from the finance review this morning.

First, the summary should say how many entries went into it. They want to see
`count` next to the total so they can spot a month where the importer dropped
rows — a month with a plausible total and forty entries instead of four hundred
is the failure mode they keep missing.

Second, the detail table under each summary wants to show every entry's share
of the month. The simplest thing is to have `summarise` write the month's total
onto each entry as it goes — set `entry["month_total"]` while you are summing —
and then the table can just read it off the entry and divide. Please wire that
up.

`docs/ledger.md` is the page the finance team reads, so keep it true.

# Your job
Choose the next action and write the brief for it. Reply in exactly this form:

ACTION: EXPLORE | WRITE | EXECUTE | DOCUMENT | REVIEW | DONE | GIVEUP
GOAL: <one sentence: what this step must achieve>
CONTEXT: <the facts that step needs, copied out in full -- it cannot see anything you do not write here>
DONE_WHEN: <how that step knows it has finished>

EXPLORE finds and reads code. WRITE changes it. EXECUTE runs things to check whether it works. DOCUMENT updates the documentation to match the change. REVIEW checks the work is right before finishing. DONE only after a successful run and a review.
```

## Raw reply

```
ACTION: EXPLORE
GOAL: Locate the source code, tests, and documentation files in the repository to understand where `summarise`, the detail table, and `docs/ledger.md` are located.
CONTEXT: The project contains a Python codebase with tests (runnable via `python -m pytest tests`) and documentation in `docs/ledger.md`. We need to find the files implementing `summarise` and the detail table.
DONE_WHEN: We have a list of relevant files and their paths, and we have read their contents.
```
