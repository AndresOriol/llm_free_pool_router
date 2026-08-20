# Turn 03 - orchestrate

_2026-08-20T15:08:35.866575+00:00_

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

# Candidate files
- /ledger/report.py
- /ledger.md

# What we know
[explore] Located `summarise` definition in `ledger/report.py`. Further files (summary generation/printing logic, detail table rendering, and tests) need to be identified.

# Steps so far
- explore: PARTIAL: Located `summarise` definition in `ledger/report.py`. Further files (summary gen

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
GOAL: Locate and read the contents of `ledger/report.py`, `ledger.md` (or `docs/ledger.md`), and locate any test files or other relevant files in the repository.
CONTEXT: The feedback from 2026-08-19 requires modifying the summary to include a `count` of entries and setting `entry["month_total"]` on each entry during `summarise`, then updating the detail table rendering and documentation. We need to find the implementation of `summarise`, the detail table rendering, and the tests.
DONE_WHEN: We have the full contents of `ledger/report.py`, the location and contents of `ledger.md` (or `docs/ledger.md`), and the location of the test files.
```
