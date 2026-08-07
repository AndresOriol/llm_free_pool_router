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
([6.14.2](../06-agent.md#6142-every-failure-is-reasoning-and-that-reframes-the-whole-exercise)).
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
([6.14.1](../06-agent.md#6141-the-pass-column-is-noise-and-i-can-prove-it)).

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
([6.14.5](../06-agent.md#6145-v7-orchestrated-hub-and-spoke)):

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
  storytelling — and the pass-rate story that [6.14.1](../06-agent.md#6141-the-pass-column-is-noise-and-i-can-prove-it)
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
  ([6.13.1](../06-agent.md#6131-the-one-failure-is-the-interesting-part)), and
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

Draft. Six roles named so far; the next session refines them.

| Role | Job | Tier (C2) |
| --- | --- | --- |
| **Orchestrator** | Decides who acts next, and hands them the context they need. Holds no tools | Wide — it cannot decide on a narrowed view, and it is the only role that curates for the others |
| **Explorer** | Finds and reads the relevant code | Small; may split further later |
| **Writer** | Applies the change | Small |
| **Executor** | Decides *what to run* to prove the code works, writes throwaway probe scripts, reports evidence | Small, but it holds the shell (§4.2) and it does think — this is not a fixed test step |
| **Documenter** | Updates the docs from the diff (R6) | Wide — it needs the diff *and* the docs |
| **Reviewer** | Checks the work before it is called done | Wide; the role most likely to split into several |

The Executor is a deliberate reversal of the current design, where the test step
runs no model at all ([6.12.1](../06-agent.md#6121-the-roles-and-the-transitions)).
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
| `report_back` | What the Orchestrator expects returned, and in what shape | A role that works and reports nothing — an observed bug ([6.14.4](../06-agent.md#6144-two-bugs-the-variants-exposed)) |

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
whole cycle ([6.13.1](../06-agent.md#6131-the-one-failure-is-the-interesting-part)).
A role must be able to say "you didn't give me enough, and here is what is
missing" — that turns a wasted cycle into a cheap, informative one.

Open before this is built: whether the envelope is prose or JSON (JSON is
checkable but small models fumble structure — the `write_todos` lesson in
[6.5](../06-agent.md#65-the-librarys-prompts-and-what-they-cost-us)), how
`context` is capped, and whether the Orchestrator writes it itself or assembles
it from the blackboard mechanically.

---

## Changelog

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
