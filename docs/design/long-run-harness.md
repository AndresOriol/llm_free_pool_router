# Design note — the ad-hoc harness as a long-run agent

*Working document. Not a wiki page: this is where the design gets argued before
anything is built, and it is expected to change every session. The settled parts
graduate into [6. The coding agent](../06-agent.md) and
[13. Roadmap](../13-roadmap.md); everything here is provisional until it does.*

**How we work on it.** Andrés sets direction and reviews conclusions; the code is
delegated. ❓ marks an open question. Nothing gets built from a section until it
is recorded in [§5 Settled](#5-settled).

---

## 1. What is actually being built

Not "an agent that does a long task". A **standing maintainer for several
projects at once**, on a daily cycle:

```
   morning                      the rest of the day
   ───────                      ───────────────────
   human reads the notes  ──▶   agent picks up the notes
   and the docs                 works the project, unattended, on its own branch
   merges yesterday's branch    writes a rationale of what it did
        ▲                       updates the project's documentation
        └──────────────────
```

The human reviews **prose, not code** — rationales and the project's own docs —
merges or discards the branch, and replies with more notes. That is the entire
interface.

Three consequences, and they are the design:

1. **Documentation is a deliverable, not a byproduct.** It is the review surface.
   A run that changes code and leaves the docs stale has failed even if the code
   is right.
2. **The unit of work is a project's notes file, not a task string.**
3. **The harness is project-agnostic** — configured by the repo it is pointed at,
   which is already how the agent's prompt works
   ([6.3](../06-agent.md#63-the-agents-instructions)).

Wall-clock time and token count are explicitly not costs to minimise. The bet
underneath: **many attempts by a weak model can substitute for one attempt by a
strong one**. Cheapness is therefore a means, not the goal — cheap steps are what
make many attempts affordable.

## 2. Constraints (facts, not preferences)

**C1 — Per-call size is capped, and the cap differs by pool member.** Groq's
cheapest members top out at 6,000 input tokens; Gemini's run to six figures. The
router excludes any provider whose ceiling the request does not fit
([4.2](../04-failover.md#42-size-aware-selection)). No amount of patience buys a
bigger single call from a small member.

**C2 — Context is scarce per tier, not globally, and that is a lever.** Splitting
work into small pieces so it fits Groq can starve a role of the breadth it needs
to decide *anything*: an orchestrator choosing what to do next, or an evaluator
judging whether the work landed, are exactly the roles whose input cannot be
narrowed without making them guess. The pool already contains the answer — the
wide-context members — so **roles are assigned to tiers deliberately**: breadth
where judgement lives, small and cheap where the work is mechanical. The cost is
that the wide-context tier is the request-scarce one, so it must not be spent on
roles that would have fit anywhere.

**C3 — The measured bottleneck is judgement, and only execution can check it.**
12 of 13 recorded failures were `reasoning`; zero `retrieval`, zero `tooling`
([6.14.2](../06-agent.md#683-every-failure-is-reasoning)).
Every variant found the file, edited it, ran the tests — and was conceptually
wrong, usually the same way. So more attempts help only if something *tells them
apart*, and that discriminator has to be stronger than what it judges. Inside a
run, executing code is the only such signal available; a pool model voting on
pool output is not.

**C4 — The review surface is the agent's own account of itself.** Reviewing prose
rather than code means the reviewed artefact is the thing C3 says cannot be
trusted: a confidently wrong rationale reads exactly like a correct one. R5 and
R6 exist to answer this.

**C5 — The current measuring instrument cannot see any of this.** One L0
single-file bugfix, exhausted as a discriminator
([6.14.1](../06-agent.md#682-the-pass-column-is-noise)).

## 3. What "helpful" requires (draft — v3)

- **R1 — Every model call fits a pool member, and roles declare which tier they
  need.** Most calls stay small enough for anything in the pool; the roles that
  need breadth (C2) declare it and are routed to wide-context members only.
- **R2 — A session survives its own process dying and resumes where it stopped.**
  Over hours, crashes and exhausted quotas are certainties, not risks.
- **R3 — A session never blocks on a human, and never ends silently on being
  stuck.** Nobody is watching until morning, so a question gets written down, not
  asked.
- **R4 — Repeated attempts are discriminated, not just produced** (C3). The core
  of the many-weak-agents bet.
- **R5 — The rationale is grounded in evidence, not recollection**: every claim
  traces to a command that ran and its exit code (C4).
- **R6 — Documentation is updated by a role that reads the diff**, not by the
  actor summarising itself from memory — the actor's account of its own work is
  the least reliable input available (C4).
- **R7 — Notes in, notes out**: the session reads the project's feedback file and
  appends its account to it. This is the whole human interface.
- **R8 — Every session lands on its own branch and leaves the main line
  untouched.** The merge is the gate that reading prose alone cannot be.

R5 and R6 are the direct answer to C4, and the part I am least confident in —
also the cheapest to get wrong invisibly, since bad docs still read fine.

## 4. What the agent is allowed to run

### 4.1 Git

R8 reverses *"No git. The agent cannot commit"*
([6.2](../06-agent.md#62-the-blast-radius)). The human stays in the loop; the
gate moves from "cannot commit" to "cannot merge", which lets a session verify
its own work and still leaves a one-command undo.

The cost is a third allowed binary next to `python`/`pytest`, so it is a
**subcommand allowlist**, not a git tool:

- **Allowed:** `status`, `diff`, `log`, `add`, `commit`, `checkout -b`, `branch`.
- **Refused:** `push`, `merge`, `rebase`, `reset --hard`, `clean`, checking out
  an existing branch — anything touching a remote or destroying committed work.

Commits are **incremental**, one per completed unit, which doubles as R2's
checkpoint and gives a readable history of what was tried. Squashing at merge
time is the human's call, not the agent's.

### 4.2 Bash, for the Executor

The Executor needs a shell: it decides *what to run* to convince itself the code
works, and writes throwaway scripts to probe behaviour the test suite doesn't
cover. A subcommand allowlist is not available here the way it is for git —
bash is arbitrary by construction.

**This is less of a change than it appears.** The current allowlist is
`python`/`pytest`, and [6.2](../06-agent.md#62-the-blast-radius) already states
the consequence plainly: `python` *is* arbitrary code execution, so what exists
today is "a small blast radius, not a sandbox". Allowing bash does not open a
door that was locked; it stops pretending the door was locked. What it changes is
convenience — and the honest response is the one that page already names:
**for real isolation, run the session in a container.** That becomes a
prerequisite the day sessions run unattended overnight, not a nicety.

Two rules survive from what the variants already taught
([6.14.5](../06-agent.md#64-the-graph)):

- **Verdicts come from exit codes, not from the Executor's summary of them.** A
  model reporting "everything passes" about a failing run would otherwise end a
  session — and under prose-only review, would end it convincingly.
- **Output is captured verbatim**, because R5's "every claim traces to a command
  that ran" is only true if the command's output is still on disk to trace to.

## 5. Settled

Reopen only with new evidence.

| Decision | Rationale |
| --- | --- |
| The team is **sequential** — one attempt in flight at a time | Concurrency buys little while requiring shared cooldown state and much harder failure reproduction |
| The human reviews **rationales and documentation, never code** | The stated model of use. Drives R5, R6 and all of C4 |
| The harness is **project-agnostic**, driven by a per-project notes file | The goal is managing several projects, not one |
| **Branch per session, incremental commits; the human merges.** Restricted git | Moves the human gate from "cannot commit" to "cannot merge" (§4) |
| **Crash-resume is in scope from the first iteration** (R2) | Without it, early long-run experiments mostly measure crashes |
| **Claude Code is the judge**, eval-time only, against a pinned rubric | Prose cannot be scored automatically, and the judge must be stronger than what it judges (§6) |
| **J2 is built first, as a Claude Code skill; J1 comes out of what J2 finds** | Error analysis precedes judge design. A rubric written before the failures are understood is the mistake §6.3 already caught once |
| **J2 never runs on the free pool** | It is the instrument used to *build* the system; an unreliable system cannot supply its own diagnosis |
| **Every report is kept** | The sequence of reports is the project's memory of what was tried and believed at the time |
| **The per-run post-mortem is a subagent, not a skill; J2 stays a skill** | Different unit and different context budget. One run per isolated context, several per batch (§9.4) |
| **The instrument is fixed before the post-mortem is built** | The evidence could not say what a role was given, so a reviewer built on it would have concluded "cannot determine" on every step worth explaining (§9.2) |
| **The post-mortem returns a narrative with citations, never a score** | Pass rate is already noise at these sample sizes; a second number would be a second thing to over-read (§9.5) |
| **Every post-mortem is kept, like every report** | Same reasoning: a diagnosis that quietly stops being true is itself a finding |

## 6. The evaluation process

This section is the one that decides whether the project improves, so it is
specified in more detail than the rest. The shape is borrowed from current
practice ([§6.6](#66-what-is-borrowed-and-from-where)) and adapted to a pool that
cannot afford production-scale sampling.

### 6.1 Three layers, evaluated by three different means

Conflating these is the usual mistake. Each layer has a different natural
instrument, and only one of them needs a model.

| Layer | Question | Instrument | Cost |
| --- | --- | --- | --- |
| **Mechanics** | Did the machinery behave? Call fits a member, killed process resumes, stuck session reports, git allowlist refuses, doc update touches what the diff touched | Code assertions. Deterministic | Free, every commit |
| **Outcome** | Did the work land? | Hidden `fail_to_pass` / `pass_to_pass` suites, already built ([8.5](../08-evaluation-method.md#85-the-run-lifecycle)) | Quota only |
| **Account** | Is the rationale true, and are the docs faithful to the diff? | Claude Code, rubric, blind | Paid, cached by diff hash |

The rule behind the table: **code-based checks for anything deterministic, a
model only for what is genuinely subjective.** Every check that migrates from the
third row to the first is permanently cheaper and permanently less noisy.

### 6.2 Two judges, not one

The single most useful distinction found in current practice is that *grading*
and *diagnosing* are different jobs, run at different frequencies, on different
inputs. Collapsing them produces a per-run score that nobody can act on.

**Build order is J2 first.** J1 is the harder of the two and will be unreliable
for a long time — a rubric is only as good as the understanding of the failures
it scores, and that understanding does not exist yet (§6.3). So J2 is built
first, on runs already recorded, and **J1's rubric is derived from the taxonomy
J2 produces** rather than guessed in advance. Until J1 clears the labelling gate
(§6.4) its verdicts are read as commentary and **gate nothing**.

**J2 — the analyst.** Per *batch*, not per run. A **Claude Code skill**, so the
procedure is versioned, invocable by name, and identical every time it runs —
which is what makes two reports months apart comparable. It reads every failed
run's evidence and does what a human would with a stack of traces: open notes per
failure, then grouping into named categories with counts. Its output is not a
score — it is **insight, a proposed update to the failure taxonomy, and a ranked
list of what to change next.**

**J1 — the grader.** Per run, once there is something worth grading. Sees the
task, `diff.patch`, verification output, the rationale, the doc diff, and the
reference solution. Blind to which configuration produced it, runs shuffled.
Returns **binary verdicts with a written critique before each verdict**, not a
0–4 score:

- `correct_for_the_right_reason` — or did it satisfy the visible tests by
  coincidence?
- `faithful` — does the rationale describe the diff that actually exists?
- `sufficient` — could a reviewer decide to merge from the prose alone?
- `docs_current` — does the documentation match the code after the change?
- `in_scope` — did it change only what the notes asked for?

Binary rather than graded, because binary is the only form that can be validated
against your labels (§6.4), and because the difference between a 3 and a 4 is not
a thing two raters agree on.

J1 without J2 gives you a leaderboard. J2 is the thing that tells you what to
build, which is why it is first and why it does not depend on J1 existing.

### 6.3 Why the existing taxonomy needs subdividing, urgently

The four classes in [10.3](../10-metrics.md#103-failure-taxonomy) —
`retrieval` / `tooling` / `reasoning` / `stopping` — were written *before* the
data. The data came back **12 of 13 in one bucket**. A taxonomy where one
category holds 92% of the mass is not a diagnosis; it is a rename of "failed".

This is precisely the situation the open-coding step exists for. `reasoning`
plausibly contains at least: *misread the requirement*, *fixed the symptom the
visible test names rather than the described behaviour*, *correct locally but
wrong for the codebase's conventions*, *did the first thing that made the test
green*. Those have different fixes — respectively prompt, hidden-test design,
context breadth, and the discriminator (R4). Today they are indistinguishable,
so every proposed fix aims at a bucket rather than a cause.

**This is the first thing J2 should be pointed at, and it can be done on runs
already recorded.** No new quota required.

### 6.4 How conclusions come back, and how you give feedback

Each batch produces one file — `evals/results/reports/<date>-<topic>.md`. All of
them are kept; the sequence is the project's record of what was believed when,
and a claim that quietly stops being true between two reports is itself a
finding.

The report is **insight, not a form**. Its job is to tell you what happened and
why it is believed, and to hand you the thread to pull if you disagree. Three
properties matter more than any structure:

- **Every claim cites its evidence** — the specific `run_id`, the line in
  `trace.jsonl`, the hunk in `diff.patch`. Not "the agent often edits before
  reading"; *"in `retry-after-case_..._r2`, `edit` fired at trace line 41 with an
  empty note (also r1, r3)"*. The point is that you can open it yourself and
  reach your own conclusion.
- **Recommendations state how they would be measured**, or they don't get made.
  This is the only rule I would keep as a hard one — it is what stops a report
  becoming a list of plausible ideas.
- **What the evidence cannot support gets said out loud.** The claims J2 wanted
  to make and couldn't. Without this, an automated analysis drifts into confident
  storytelling — and the pass-rate story that [6.14.1](../06-agent.md#682-the-pass-column-is-noise)
  had to retract is the local proof that it happens here.

The one mechanical part is the **verdict** — promote / draw / reject, computed
from the promotion rule ([8.7](../08-evaluation-method.md#87-the-promotion-rule))
and stated before any narrative, so the narrative cannot colour it.

**Your feedback is an open comments section at the end of each report.** Write
freely: disagreement with a diagnosis, a direction the analysis missed, a
recommendation you don't want pursued. It stays in the file, so the next report
is written by something that has read your last one.

That section is also the raw material for judging the judge later. Once J1
exists, its verdicts get compared against your judgement on a sample, reporting
**true positive and true negative rate separately** — never overall agreement,
which hides a judge that says "fine" to everything. Until that comparison has
been done, J1's output is commentary and gates nothing.

### 6.5 What a long-run agent needs measured that a task-runner does not

The current metric set ([10.2](../10-metrics.md#102-automatic-metrics)) assumes a
run is short and either works or doesn't. Three additions follow from §1:

- **Sustained-work horizon** — how long the session does useful work before its
  first unrecoverable derailment. For an agent whose selling point is running
  unattended, this is closer to the headline number than pass rate is; it is also
  how the wider field now tracks agent progress.
- **Degradation across a session** — quality of the last unit of work versus the
  first. Long iterative runs are known to decay, and a decay curve is invisible
  to an end-state pass/fail.
- **Spec-gaming gap** — visible tests passing while hidden tests fail. This
  project has already produced a textbook instance
  ([6.13.1](../06-agent.md#684-closing-the-loop-is-not-the-same-as-being-right)), and
  under a prose-only review model it acquires a second face: a rationale that
  reads better than the diff deserves. J1's `faithful` verdict is the probe for
  it.

### 6.6 What is borrowed, and from where

| Idea | Source | How it is adapted here |
| --- | --- | --- |
| Error analysis *before* judge design — open-code traces, then axial-code them into a taxonomy with counts; a single owner holds the taxonomy | [Hamel Husain & Shreya Shankar](https://hamel.dev/blog/posts/evals-faq/) | Adopted, inverted for scale: industry samples ~100 of thousands of production traces. Runs here are scarce and expensive, so **every failure gets read**, and the constraint becomes producing enough varied failures to learn from — which is another argument for scenarios past L0 |
| Binary judgements with a critique, never Likert | [Hamel Husain](https://hamel.dev/blog/posts/llm-judge/) | Adopted. Replaces the 0–4 scales currently specified in [10.4](../10-metrics.md#104-the-judge) |
| Validate the judge against human labels by TPR/TNR on a held-out set, not accuracy | [Hamel Husain](https://hamel.dev/blog/posts/evals-faq/) | Adopted as the gate in §6.4. Your labels are the ground truth |
| Code-based evals for deterministic failures; a model only for subjective ones | [Hamel Husain](https://hamel.dev/blog/posts/evals-faq/) | Adopted as the §6.1 layering |
| Three evaluation layers — session outcome, trace quality, tool-level correctness | [LLM-as-judge agent patterns](https://zylos.ai/research/2026-05-26-llm-as-judge-agent-evaluation-patterns/) | Adopted as §6.1, with "trace quality" reinterpreted as *the rationale and docs*, since those are the review surface here |
| Agent-as-a-judge: a judge that reads the whole trajectory, not just the end state | [survey](https://arxiv.org/pdf/2508.02994) | Partially. J2 reads trajectories; J1 stays diff-addressed and cacheable. Full trajectory judging is the expensive version and is not justified yet |
| Task-adaptive rubrics correlate with humans far better than one static rubric | [AdaRubric](https://arxiv.org/pdf/2603.21362) | Noted, **not adopted**. A rubric that varies per task cannot be pinned, and pinning is what makes scores comparable across epochs ([10.4](../10-metrics.md#104-the-judge)). Revisit only if J1 fails its TPR/TNR gate |
| Visible validation suite vs held-out behavioural suite, to expose reward hacking | [SpecBench](https://arxiv.org/pdf/2605.21384) | Already the design ([9.3](../09-scenarios.md#93-anatomy)); the finding is that this is now standard practice, which raises confidence in it |
| Measure agent progress by how long autonomy is sustained, not by benchmark score | METR-style horizon measurement | New metric, §6.5 |
| Quality decays over long iterative runs | [SlopCodeBench](https://arxiv.org/html/2603.24755v1) | New metric, §6.5 |
| Don't build an eval until the failure justifies the cost | [Hamel Husain](https://hamel.dev/blog/posts/evals-faq/) | Adopted as the reason §6.1's first row is code, not model |

### 6.7 One conflict of interest, stated

Claude Code writes this harness and also grades it. The grader being blind to
configuration ([10.4](../10-metrics.md#104-the-judge)) covers the obvious half.
The other half is not covered by any rule: J2's *recommendations* come from the
same place as the changes being recommended, so a blind spot in the design is a
blind spot in its diagnosis. Section 6.4's "what the evidence cannot support" and
your labelled disagreement are the only real checks on it — which is a reason to
take the labelling seriously rather than rubber-stamp it.

Settled, and worth stating as a principle rather than a preference: **J2 never
runs on the pool.** It is the instrument used to build the system, and a system
that is not yet reliable cannot be trusted to diagnose itself. The moment that
changes is not a cost decision.

## 7. Router changes this implies

Sketch only. The first two are obviously right; the third is the one that matters
most and is the least defined.

- **Wait instead of exhausting.** With time free, "all providers cooling down"
  should be a sleep, not an error.
- **Permanently disable decommissioned models** rather than benching them
  temporarily ([13.2](../13-roadmap.md#132-what-to-do-next)).
- **Tier-aware selection (C2).** A request must be able to say *"this needs a
  wide-context member"* and have the router honour it, and conversely the pool
  must not spend request-scarce wide-context members on work that would have fit
  anywhere. Priority is a single global number today, so this is a real change to
  the selection rule — and the one router change that serves judgement rather
  than throughput.

## 8. The roles

**Built**, and now the only architecture
([graph.py](../../agent/harness/graph.py), [roles.py](../../agent/harness/roles.py)).

| Role | Job | Tools | Tier (C2) |
| --- | --- | --- | --- |
| **Orchestrator** | Decides who acts next and writes their brief | none | Wide — it cannot decide on a narrowed view, and it is the only role that curates for the others |
| **Explorer** | Finds and reads the relevant code | find, search, read, list | any |
| **Writer** | Applies the change | read, replace, create | any |
| **Executor** | Decides *what to run* to prove the code works, writes throwaway probe scripts | run_command, create | any; holds the shell (§4.2) |
| **Documenter** | Updates the docs from the diff (R6) | read, find, replace, create | Wide — needs the diff *and* the docs |
| **Reviewer** | Checks the work before it is called done | read, search | Wide; most likely to split further |

Three edges are deterministic, and each one is a model call not spent on a
decision that only has one right answer: **after an applied edit, run the code**;
**a `DONE` with nothing executed is refused**; **a `DONE` with nothing reviewed
is refused**. Commits, the journal and the diff are likewise driven by the
session rather than by a role that could forget.

The Executor is a deliberate reversal of the current design, where the test step
runs no model at all ([6.12.1](../06-agent.md#64-the-graph)).
That was right when the only check was a fixed test command. It is wrong once
verification means *deciding what would convince you* — which is a judgement, and
the one place a model earns its call.

### 8.1 The handoff envelope

This is the answer to the problem that opened this document: splitting work to
fit small models starves each piece of the context it needs to be right (C2).

The resolution is **the Orchestrator pushes context down; roles do not pull it
up.** Breadth is centralised in the one role that has the tier for it, and every
other role receives a self-contained brief. Two things follow, and both are load
bearing:

- A role's prompt size is bounded by **what the Orchestrator chose to include**,
  not by how long the session has run. That is what keeps C1 satisfiable at step
  40.
- A small model told to "go read the notes for background" mostly won't, and will
  act on what it has. Pushing removes the failure rather than instructing against
  it.

So a handoff is a structure, not a sentence. First draft — deliberately small,
and every field earns its place by naming a failure already seen:

| Field | What it carries | The failure it prevents |
| --- | --- | --- |
| `goal` | One sentence: what this step must achieve | A role inventing its own objective |
| `why` | How it serves the session's aim | Locally correct, globally wrong — the `reasoning` bucket's likely largest tenant (§6.3) |
| `context` | The facts, **copied in**, not referenced | The role acting blind because it didn't fetch |
| `inputs` | Concrete pointers: paths, symbols, prior findings | Re-deriving what an earlier role already found |
| `constraints` | What not to touch | Scope creep, which J1 scores as `in_scope` |
| `done_when` | The check that ends this step — for the Executor, a command and its expected exit | "Done" meaning "I stopped" |
| `report_back` | What the Orchestrator expects returned, and in what shape | A role that works and reports nothing — an observed bug ([6.14.4](../06-agent.md#66-one-role-call)) |

And the return path, which matters as much as the outbound one:

| Field | What it carries |
| --- | --- |
| `status` | `done` \| `partial` \| `blocked` \| `insufficient_context` |
| `evidence` | Commands run with exit codes, or `file:line` — the substrate R5 needs |
| `finding` | Prose, capped |
| `suggestion` | Optional, non-binding: what this role thinks should happen next |

`insufficient_context` is the field I would fight for. Pushing context makes the
Orchestrator a single point of failure for every role's quality — curate badly
and the role is blind. Today a blind role *guesses*, and there is a recorded run
where `inspect` returned no finding and `edit` then applied nothing, burning a
whole cycle ([6.13.1](../06-agent.md#684-closing-the-loop-is-not-the-same-as-being-right)).
A role must be able to say "you didn't give me enough, and here is what is
missing" — that turns a wasted cycle into a cheap, informative one.

**Built as labelled plain text, not JSON** — `ACTION:` / `GOAL:` / `CONTEXT:` /
`DONE_WHEN:` down, `STATUS:` / `FINDING:` up, parsed leniently with a
deterministic fallback. JSON is checkable, but the `write_todos` lesson
([6.5](../06-agent.md#61-what-it-is)) is that
small models fumble structure, and a session must not end because a model wrote
a sentence where a word was asked for.

### 8.2 What the first live run showed

The first run against the real pool ended in none of the ways the unit tests
cover, which is the argument for running it. Three faults, all now fixed and
each with a regression test:

- **Labels were only parsed at the start of a line.** Models put the whole reply
  on one line — `STATUS: DONE | FINDING: ...` — so every finding in the journal
  began with the literal text of its own status label.
- **The Writer could not read.** `replace_in_file` needs `old_text` to match
  exactly, and the role had no read tool, on the theory that applying a change
  needs no search. It reported, verbatim: *"no tool for reading files is
  provided"*. Reading is now in its tool set; searching still is not.
- **The orchestrator explored forever.** Nine of twelve steps were `explore`,
  re-reading the same four files. Reading is the move an orchestrator can always
  justify, so a third consecutive one is now refused once files are known.

And one finding that is *not* a bug, and matters more than the three that were.
An `explore` role — holding no edit tool and no shell — reported: *"Implemented
support for spelled-out units… All tests pass (4 passed)."* Nothing had been
implemented and nothing had run. This is C4/C5 arriving on schedule, in the
first live run, from a role structurally incapable of doing what it claimed. It
is the reason acting roles are judged by their tool effects and the rationale is
built from the journal rather than from what anyone said.

---

## 9. Reading one session back: the post-mortem

*Added after auditing what the first batch's evidence could and could not
answer. The audit's conclusion is §9.2, and it is the reason this section
exists at all.*

### 9.1 J2 is the wrong granularity for "why"

J2 works on a **batch**: it counts categories and ranks what to change. That is
the right shape for deciding where to spend the next change, and the wrong shape
for understanding a single derailment — the bundle it reads clips each run to a
few thousand characters, which is enough to see *that* a run went wrong and not
enough to see *where it turned*.

J2's own procedure asks for "one note per failed run, in your own words, before
any categorising". Those notes are currently improvised from the clipped bundle.
A **per-run post-mortem** produces them properly, and J2 then works over the
post-mortems rather than over raw evidence. So this is not a competing judge; it
is the step that supplies J2's first input.

| | Post-mortem | J2 | J1 |
| --- | --- | --- | --- |
| Unit | one run | one batch | one run |
| Question | where did it turn, and what did it have | what fails most, what to fix | is this run acceptable |
| Output | a narrative with citations | a taxonomy with counts | binary verdicts |
| Exists | being built | built | not built |

### 9.2 The finding: the evidence could not say what a role was given

The first batch's report has a paragraph it could not finish — an edit the
journal claims and the tree does not contain — and it names the reason: the
workdir is gone and nothing recorded the intermediate state. That is one
instance of a general hole.

`threshold-off-by-one` r1, journal step 6, is the sharper case:

```
write: INSUFFICIENT_CONTEXT: "Missing contents of alerts/rules.py to determine
                              threshold checking and message formatting"
```

A role is saying, in the exact words §8.1 designed for it, *you did not give me
enough*. **And nothing on disk said what it had been given.** The signal the
envelope exists to produce arrived, and the instrument could not read it.

Three gaps, all in the same direction:

| Not recorded | Why it matters |
| --- | --- |
| The brief's `CONTEXT` and `DONE_WHEN` — `Step` kept only `goal` | The orchestrator pushing context down is the entire mechanism of §8.1. It was the one variable not being logged |
| The prompt a role actually received | Which blackboard sections rendered, and what got clipped out of them. Whether a role was blind is not inferable from its reply |
| The model's reply before `envelope.py` parsed it | A model that wrote nonsense and a parser that mangled sense are indistinguishable afterwards. `think-leakage` was found by luck, from text that happened to survive into a finding |

The consequence is precise: on this evidence a review can say *"step 7 invented
`alerts/formatter.py` instead of editing `alerts/message.py`"* — which J2 already
said — but not *"and its brief named no file, so inventing one was the only move
available to it."* The first is a report. The second is a recommendation. Only
the second changes anything.

**This generalises past the post-mortem.** Every proposal in §8.1 is a claim
about what a role should be handed; none of them could have been evaluated
against a recorded run.

### 9.3 What is now captured

Three additions, none of which touches a decision the harness makes:

- **`.harness/steps/NN-<role>.md`, one file per model turn** — the brief it was
  given, the prompt it received, its raw reply, and its tool calls with their
  outputs. The orchestrator's own turns are included: its reply *is* the brief,
  so the decision and its inputs sit in one file. Written by `Transcript` in
  [record.py](../../agent/harness/record.py); a resumed session continues the
  numbering rather than overwriting the turns that preceded the crash.
- **`journal.jsonl` carries the whole brief** — `context` and `done_when`
  alongside `goal`, so the cheap artifact stays sufficient for counting and the
  expensive one is only opened when a step needs explaining.
- **`trace.jsonl` records each provider's reply text**, clipped like every other
  field. This is the per-*attempt* view, so it also attributes a malformed reply
  to the pool member that produced it.

Cost is about 5 KB per turn, ~70 KB for a fourteen-step session. Deliberately
*not* folded into the journal: the journal is the crash-resume substrate, re-read
line by line on every resume, and it should stay cheap to read.

**The transcript earned itself on its first output.** The deterministic
post-write `EXECUTE` brief carries `CONTEXT: ok: replaced 1 occurrence(s) in
/calc.py` and no test command — the Executor is asked to decide what would
convince it while being told only that something changed somewhere. That is
visible in one file now and was invisible before. ❓ Whether the forced briefs
should carry more is the first question the post-mortem should be pointed at.

### 9.4 A subagent, not a skill

J2 is a skill because it is invoked once per batch, in the main context, and its
output is the thing the human reads next. The post-mortem is neither:

- **Context.** A session run is fourteen steps, ~100 provider calls, plus the
  diff, the hidden-test output and the rationale. Reviewing several in one
  context exhausts it before the synthesis, and the synthesis is J2's job
  anyway.
- **It is a loop, not a call.** batch → one post-mortem per run → J2 over the
  post-mortems → a change → repeat. That wants something launchable per run,
  with its own context, several times.

So: a subagent that writes **one file per run** to
`evals/results/reviews/<run_id>.md`, and J2 reads that directory instead of
improvising its open-coding notes. Reviews are kept forever, on the same
reasoning as reports (§5): a diagnosis that quietly stops being true between two
runs is itself a finding.

### 9.5 What it must produce, and what it must not

**Not a score.** Pass rate is already noise at these sample sizes, and a second
number would be a second thing to over-read. Quality is currently poor enough
that the useful output is a description of *how* it went wrong, not a position
on a scale.

But "not a score" cannot mean "free-form", or two post-mortems are not
comparable and the sequence stops being a record. What is pinned is the
**questions**, not a scale:

1. **What was it asked to do**, and what would have counted as done.
2. **The turn-by-turn account** — what happened, in the order it happened.
3. **The first turn that could not be recovered from**, named by turn number.
   Not the first mistake: the first one the rest of the session could not undo.
4. **What that turn had** — its brief, and what its prompt did and did not
   contain. This is the question the whole instrument change exists to serve.
5. **What it would have needed** to go the other way.
6. **What in the harness would have supplied that** — a role's tools, a
   blackboard section's cap, a deterministic edge, the orchestrator's brief. If
   the honest answer is "a better model", it says that instead of inventing a
   mechanism.

Every claim cites `steps/NN-<role>.md`, `journal:N`, or a line in `trace.jsonl`,
on the same rule as §6.4: the human must be able to open it and disagree.

❓ Open: whether the post-mortem should also read runs that **passed**. A pass
that happened for the wrong reason is exactly what §6.5's spec-gaming gap
describes, and it is invisible to an analysis that only reads failures.

### 9.6 Where the loop stands

```
scenarios ──▶ batch ──▶ post-mortem per run ──▶ J2 over the batch ──▶ change
    ▲                                                                   │
    └───────────────────────────────────────────────────────────────────┘
```

Two links are weak, and they are weak for the same reason. **The scenario set is
three, one of them exhausted** (§6.3 needs varied failures to code, and there
are not enough). And **five of the eight categories in
[9.6](../09-scenarios.md#96-categories-to-cover) have never been run at all** —
including `trap`, which is the only probe of over-eagerness, and symptom-only
bugfix, which is the reason no `retrieval` failure has ever been observed: every
scenario so far hands over the file.

A second Gemini account changes what is affordable here. Reps of five per
scenario stop being extravagant, and enough varied failures to code is the
binding constraint on the whole loop.

---

## Changelog

- **v7** — added §9, the per-run post-mortem. Audited what the first batch's
  evidence could answer and found the gap that motivates it: nothing recorded
  what a role was *given*, only what it concluded (§9.2). Instrumented in the
  same pass — a per-turn transcript, the whole brief in the journal, the raw
  reply in the trace. Settled that the post-mortem is a subagent and that J2
  reads its output rather than raw evidence.
- **v6** — the Executor gets a model and a shell (§4.2, §8); §4 broadened to
  cover everything the agent may run. Added §8.1, the handoff envelope: the
  Orchestrator pushes curated context down, roles can answer
  `insufficient_context` rather than guess.
- v5 — J2 is a Claude Code skill and is built *before* J1, with J1's rubric
  derived from what J2 finds; J2 never runs on the pool. §6.4 rewritten as
  insight-with-citations plus an open comments section rather than a fixed form.
  Added §8 as a placeholder for the roles.
- v4 — §6 expanded into the evaluation process: three layers by three
  instruments, grader/analyst split, the report format and the labelling loop
  that validates the judge, long-run metrics, and what was borrowed from current
  practice. Recorded that the existing taxonomy is 92% one bucket and must be
  subdivided first.
- v3 — pruned. C2 became context-tier scarcity; old C3/C4 merged; requirements
  are prose; git section shortened, incremental commits settled.
- v2 — settled the acceptance gate, crash-resume, and the rubric judge.
- v1 — reframed around the real operating model.
- v0 — first sketch.
