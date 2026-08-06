[← Wiki index](README.md)

# 6. The coding agent

*What the agent can do, what it is not allowed to do, which of its behaviours
are ours versus inherited from the library — and what it would take to make it
better at code generation.*

Sections [6.8](#68-why-the-agent-underperforms-the-measured-diagnosis) onward
are **proposals under review, not built.** They are marked as such.

## 6.1 What it is

A [deepagents](https://github.com/langchain-ai/deepagents) coding loop whose
single model is the pool. That one sentence is the whole design: every step of
every task routes through the failover logic, so a rate limit hit mid-task
reroutes transparently and the run continues.

```bash
python -m agent.coding_agent [workdir]           # interactive
python -m agent.coding_agent workdir < brief.md  # one-shot
```

`workdir` defaults to the current directory. In interactive mode, type a task at
the `>` prompt; `exit` quits. In one-shot mode the piped text is the whole task
and the process exits at EOF — which is what makes it drivable from a script or
from an orchestrating agent ([12.5](12-development-harness.md#125-driving-the-free-agents)).

## 6.2 The blast radius

Two independent restrictions, both worth understanding before pointing this at
anything you care about:

**Filesystem jail.** The backend runs with `virtual_mode=True` rooted at
`workdir`. The agent's virtual `/` *is* the workdir; absolute paths and `..`
cannot escape it. A brief should therefore tell the agent to write at `/`, not
at a host path.

**Execution allowlist.** The agent can run its own tests via the `execute`
tool, but only `python` and `pytest`, with `shell=False`, `workdir` as cwd,
secret env vars stripped, output capped at 100 KB and a 300s timeout
([restricted_backend.py](../agent/restricted_backend.py)).

This is a **small blast radius, not a sandbox**. `python` is arbitrary code
execution. For real isolation, run the whole agent inside a container.

The custom backend exists specifically to avoid deepagents' `LocalShellBackend`,
which implements the same protocol with no allowlist at all. Subclassing
`FilesystemBackend` and adding a restricted `execute` is the intended
customization point.

**No git.** The agent cannot commit. Whoever drives it reviews the diff and
commits — which is also what keeps a human in the loop on anything the agent
produces.

## 6.3 The agent's instructions

At build time, `coding_agent.py` reads `CLAUDE.md` — or, failing that,
`AGENTS.md` — from the workdir and passes its raw text as the deepagents
`system_prompt`. First match wins; if neither exists, the prompt is empty.

Two properties follow:

- The agent's behaviour on a target project is configured by a file **in that
  project**, not by this repo. Pointing it at a different codebase gives it
  that codebase's conventions.
- It is loaded **once** and never refreshed mid-run. Editing `CLAUDE.md` during
  a run has no effect.

Whether a codebase ships a `CLAUDE.md` at all is itself a variable worth
measuring, which is why scenarios record it as `context_mode`
([9.4](09-scenarios.md#94-scenarioyaml)).

deepagents composes the final prompt as `USER → BASE → SUFFIX`: our text first,
then the library's own base agent prompt, then a harness-profile suffix. Because
ours sits first, it wins on conflict — but the base prompt is always present
underneath, and today the `SUFFIX` slot is empty
([6.5](#65-the-librarys-prompts-and-what-they-cost-us)).

## 6.4 What the model actually receives

Everything downstream of `create_deep_agent` is **library defaults**. No
`skills=`, no `subagents=`, no `permissions=`, no custom `middleware=`.

The agent gets deepagents' built-in tools: `write_todos`, the filesystem set
(`ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`), `execute`, and
`task` for delegating to a subagent. Measured, that costs on **every single
step**, before any task text or file content:

| Component | ~tokens | Share |
| --- | ---: | ---: |
| `task` schema (subagent delegation) | 1,892 | 31% |
| `write_todos` schema | 1,094 | 18% |
| `execute` schema | 831 | 14% |
| `read_file` schema | 611 | 10% |
| `grep`, `edit_file`, `glob`, `write_file`, `ls` | 1,105 | 18% |
| `BASE_AGENT_PROMPT` | 564 | 9% |
| **Fixed overhead per step** | **~6,097** | |

Two things follow, and they are the whole story of this page:

1. **The system prompt is 9% of the overhead. Tool schemas are 91%.** Prompt
   engineering is not where the budget is.
2. **`task` and `write_todos` alone are 49%** — and `task` exists only because
   deepagents auto-adds a generic `general-purpose` subagent that this project
   never asked for.

Numbers are the chars/4 heuristic the router itself uses
([4.2](04-failover.md#42-size-aware-selection)); provider-reported counts in
real runs land slightly higher.

## 6.5 The library's prompts, and what they cost us

Read from the installed source, not from docs. Three specific conflicts between
what the library's prompts say and what this deployment can actually do:

**`execute` advertises a general shell that does not exist here.** Its 831-token
description opens *"Executes a shell command in an isolated sandbox
environment"* and gives worked examples — `npm install && npm test`,
`make build`, `mkdir dir && cd dir`, `;` and `&&` chaining. Our backend allows
**only** `python` and `pytest`, with `shell=False`, so none of that is
executable. The description actively misinforms the model, and we have it
failing in the trace: `gpt-oss-120b`'s first action was
`execute({"cmd": ["bash","-lc","ls -R"]})` → `tool_use_failed` → reroute.

**The base prompt tells the agent to ask blocking questions.** Its *Clarifying
Requests* section instructs it to ask "a concise blocking followup question" and
to "ask domain-defining questions before implementation questions". In one-shot
mode there is nobody to answer — stdin is already at EOF — so a clarifying
question ends the run. That is precisely the `stopping` failure class
([10.3](10-metrics.md#103-failure-taxonomy)). The same section carries
monitoring/alerting-specific guidance ("ask what signals, thresholds, or
conditions should trigger an alert") that is irrelevant to a coding agent and
paid for on every step.

**`write_todos` asks small models to plan in a format they fumble.** It costs
1,094 tokens and, in the recorded baseline, `llama-3.3-70b`'s very first action
was a malformed `write_todos` call — `<function=write_todos [...]</function>` —
which Groq rejected as `tool_use_failed`. The most expensive optional tool
produced the first failure of the run.

And because the model is a pre-built `RouterChatModel` instance rather than a
`provider:model` string, **no harness profile matches**, so none of deepagents'
own prompt-tuning machinery (`system_prompt_suffix`, `tool_description_overrides`,
`base_system_prompt`) applies. Every model in the pool gets the identical
generic prompt.

The full teardown of the middleware stack, summarization algorithm, backends and
subagent isolation is in
[.claude/reports/deepagents-architecture.md](../.claude/reports/deepagents-architecture.md).

## 6.6 Loop budget

The run config sets `recursion_limit: 150` — the ceiling on agent loop
iterations. Hitting it is a real observed failure mode, classified as
`stopping` in the failure taxonomy ([10.3](10-metrics.md#103-failure-taxonomy)),
and it is one of the things a harness change might legitimately try to improve.

## 6.7 What failover looks like in practice

Expect a single agent step to walk several small-TPM Groq models before a
higher-capacity account accepts the request. That is the design working, not a
malfunction — but it is also why the pool gets drained fast, and why
`failover_bounces` is tracked as a first-class metric
([10.2](10-metrics.md#102-automatic-metrics)).

Free tiers signal limits inconsistently — Groq uses HTTP 429 *and* 413 for
tokens-per-minute — and both are treated as transient
([4.3](04-failover.md#43-classifying-a-failure)).

---

> **Status: everything below is a proposal awaiting review. None of it is
> built.** Each item becomes a candidate configuration measured against baseline
> before it merges ([12.7](12-development-harness.md#127-the-rule-that-governs-changes-to-the-harness)).

## 6.8 Why the agent underperforms: the measured diagnosis

The instinct is that free-tier models are too weak for code generation. The
recorded baseline says the binding constraint is **token budget, not
intelligence** — and that most of the budget is spent before the task starts.

### 6.8.1 The overhead prices the Groq half of the pool out

Every step in the recorded baseline cost 6,783–7,834 input tokens for a
*one-line fix*. Against the pool's real ceilings, and the router's `0.9` fit
margin:

| Model | ceiling | step 1 (7,085 tok) | step 4 (7,536 tok) |
| --- | ---: | :---: | :---: |
| `llama-3.1-8b-instant` | 6,000 | **excluded** | **excluded** |
| `qwen/qwen3-32b` | 6,000 | **excluded** | **excluded** |
| `openai/gpt-oss-20b` | 8,000 | marginal | **excluded** |
| `openai/gpt-oss-120b` | 8,000 | marginal | **excluded** |
| `qwen/qwen3.6-27b` | 8,000 | marginal | **excluded** |
| `llama-3.3-70b-versatile` | 12,000 | ok | ok |
| Gemini / Gemma | 128K–250K | ok | ok |

Two Groq models can never serve even the first step, and by step 4 the entire
Groq side except the 70B is filtered out on size. **This is exactly what the
trace shows** — steps 3–6 of run 1 all landed on Gemini. The documented
observation that the "workhorse fallback" tier does the real implementation work
"by exhaustion, not by choice" ([3.4](03-pool-model.md#34-priority-tiers)) is not
a statement about model quality. It is this table.

Separately, even where a model *fits*, spending 7,000 of a 6,000–12,000 TPM
budget means roughly **one request per minute** from that account.

### 6.8.2 The fix is verified, not hypothetical

The blocker in the report — "no harness profile can ever match a pre-built
model" — turns out to be wrong in a useful way. Profile resolution falls back to
a **provider-only** key derived from the model's `_get_ls_params()`, which for
`RouterChatModel` already returns `routerchatmodel`. Registering a profile under
that key applies today.

Measured, on the tools the model actually receives:

| | tools the model sees | ~tokens/step |
| --- | --- | ---: |
| today | `write_todos, ls, read_file, write_file, edit_file, glob, grep, execute, task` | 5,543 |
| with a profile | `ls, read_file, write_file, edit_file, glob, grep, execute` | **1,901** |

**A 66% cut in fixed overhead.** Projected onto the observed step cost, a step
drops from ~7,085 to ~3,443 tokens, which puts **every** pool member back inside
its ceiling — including the two that are structurally excluded today — and
roughly doubles the steps-per-minute each Groq account can serve.

One caveat found while verifying: the profile key is derived from the *class
name*, so renaming or subclassing `RouterChatModel` silently disables the whole
profile. The binding should be pinned explicitly by overriding `_get_ls_params`,
not left to the default.

## 6.9 Proposed strategies

Ordered by measured leverage. Each is independently shippable and independently
measurable.

| # | Strategy | Mechanism | Expected effect | Risk |
| --- | --- | --- | --- | --- |
| **S1** | Register a harness profile for the pool | `register_harness_profile("free_pool", …)` + pin `_get_ls_params` | Unlocks S2–S4. No behaviour change alone | Low. Inert if the key mismatches — must be asserted at build |
| **S2** | Drop `task` and `write_todos` | `general_purpose_subagent=…(enabled=False)`, `excluded_tools=["write_todos"]` | **−2,986 tok/step.** Whole pool becomes usable | Loses planning scaffold; may hurt multi-step tasks. See the tension in [6.9.1](#691-the-one-real-tension) |
| **S3** | Rewrite `execute`'s description to match the allowlist | `tool_description_overrides` | −~740 tok/step **and** removes a known `tool_use_failed` cause | Low. Strictly more truthful |
| **S4** | Replace the base prompt | `base_system_prompt` | Removes monitoring/alerting guidance and the "ask a blocking question" instruction that ends one-shot runs | Medium. Wholesale replacement — must re-state the understand→act→verify loop |
| **S5** | Tune summarization to real ceilings | `middleware=[create_summarization_middleware(trigger=TriggerClause(...))]` | Prevents hard context overflow on small models before the 170k default ever fires | Medium. One static number across a heterogeneous pool is a compromise |
| **S6** | Repo-configurable skills | `skills=["/.claude/skills"]` | Target repo teaches the agent its own workflows | Low, *after* S2. Costs ~464 tok + ~1 line/skill |
| **S7** | Enable `truncate_args_settings` | Summarization pre-pass | Shrinks old `write_file`/`edit_file` payloads before paying for an LLM summarization call | Low. Good fit for a coding agent |
| **S8** | Change the edit format | Custom tool replacing `edit_file` | Only if the taxonomy shows `tooling` dominates — exact-string match is the fragile part | High effort. **Gate on data** |
| **S9** | Self-verification loop | `RubricMiddleware` | Forces a test run before declaring done; targets `stopping` | High cost — a grader call per finish, on a pool that is already the bottleneck |

### 6.9.1 The one real tension

**S2 removes the `task` tool. S7 in the architecture report recommends
*delegating exploration to subagents*, which needs exactly that tool.** They are
in direct conflict and the wiki should not pretend otherwise.

The argument for dropping it: subagent delegation costs 1,892 tokens on every
step of every task, whether or not it is ever used, and a subagent's exploration
runs on the same exhausted pool — so context isolation is bought with quota this
pool does not have.

The argument for keeping it: on genuinely large codebases, keeping exploration
noise out of the parent's context is the only thing that stops the parent
overflowing.

This is a scenario-dependent trade-off, which makes it a question for
measurement rather than for argument: run S2 with and without, on an L2
multi-file scenario. Recommendation is to **drop it first** — the current
scenario set is single-file, and 31% of the budget for an unused capability is
not defensible until there is evidence it pays.

## 6.10 Repo-level configuration

The goal: a target repository configures the agent, the same way `CLAUDE.md`
already configures its prompt.

**Today** — one mechanism: `CLAUDE.md`/`AGENTS.md` from the workdir becomes the
system prompt ([6.3](#63-the-agents-instructions)).

**Proposed** — three layers, in precedence order:

1. **`CLAUDE.md` / `AGENTS.md`** — unchanged. Project conventions, in prose.
2. **`.claude/skills/*/SKILL.md`** — `skills=["/.claude/skills"]`. Because the
   backend is jailed to the workdir, this reads skills **from the target repo**,
   which is exactly the desired property: a repo ships its own workflows
   ("how to add a provider here", "how to run this project's tests"). Progressive
   disclosure means only name + description enter the prompt; the body is loaded
   on demand via `read_file`. Cost is ~464 tokens for the preamble plus roughly
   one line per skill — affordable **only after S2** frees the budget.
3. **`.claude/agent.yaml`** (new, optional) — the knobs that are not prose:
   which tools to expose, summarization thresholds, recursion limit, whether to
   enable the `task` tool. Absent, defaults apply. This is what makes S2's
   trade-off a per-repo decision instead of a global one.

Two constraints worth stating before anyone builds this:

- **Every layer costs tokens on every step.** On a 6,000-TPM model the entire
  configuration surface has to fit in a budget that the tool schemas alone
  currently exceed. Repo-configurability is affordable *because of* S2, not
  alongside it.
- **A repo's config is untrusted input.** The agent already executes code from
  the workdir, so this is not a new trust boundary — but a `SKILL.md` is
  instructions, and it should be treated as data the operator reviews, not as
  something the agent silently obeys.

## 6.11 Proposed baseline implementation

The smallest change that makes the above measurable. One new module, one call
site, no change to `llm_router/`.

```
agent/profile.py     register_pool_profile(): the HarnessProfile for the pool
                     — excluded tools, execute description, base prompt, suffix
agent/coding_agent.py  call it in build_agent(); assert the profile actually
                     resolved (a silent miss is the failure mode to fear)
```

Sequenced so each step is separately attributable:

| Step | Change | Configuration | Success criterion |
| --- | --- | --- | --- |
| 0 | — | `baseline` | Re-baseline at n=5 first. **The current n=2 baseline is not a comparison point**, and half of it crashed on the dead-model bug ([11.4](11-eval-status.md#114-blockers)) |
| 1 | S1 + S3 | `profile-execute-desc` | `bad_tool_calls` down; `tokens_in` down ~740/step; pass rate not worse |
| 2 | S2 | `profile-lean-tools` | `tokens_in` down ~3,000/step; `failover_bounces` down; `models_used` shifts back toward Groq |
| 3 | S4 | `profile-base-prompt` | `stopping` failures down |
| 4 | S5 + S7 | `profile-summarization` | No context-overflow crashes on long tasks |
| 5 | S6 | `skills-enabled` | Only after 1–4 land |

**The dead-model crash must be fixed before any of this**
([13.2](13-roadmap.md#132-what-to-do-next)). Measuring against a baseline where
50% of runs die for unrelated reasons would produce numbers that mean nothing.

Two guardrails on the measurement itself:

- **These changes shift the model mix on purpose.** S2 is a success precisely
  when cheaper Groq models start serving steps that Gemini served before. But
  [8.6](08-evaluation-method.md#86-fair-comparison) flags a comparison as
  confounded when the mixes differ — so read `models_used` as an *outcome* here,
  not as a confound, and state that in the report.
- **`tokens_in` per step is the leading indicator** and it is already collected
  ([10.2](10-metrics.md#102-automatic-metrics)). It will move long before pass
  rate does, and with one L0 scenario it is the only signal with enough
  resolution to act on.

---

## 6.12 An alternative architecture: the ad-hoc role harness

> **Status: built, on branch `harness/adhoc-router`. Not merged, not yet
> measured against baseline on a shared scenario.**

Sections 6.9–6.11 try to make the deep-agents loop cheaper. This takes the
opposite bet: **spend more turns to make each turn small enough that the whole
pool can serve it.**

The wide loop hands every step all nine tools whether or not the step needs
them. The harness gives each step only the tools its job requires, and keeps
the run's accumulated knowledge in a shared blackboard rather than in a growing
conversation. So a role's prompt is bounded by what it declares, not by how
long the run has been going — step 40 costs what step 1 costs.

### 6.12.1 The roles and the transitions

```
locate ──▶ inspect ──▶ edit ──▶ [test] ──▶ pass? ──▶ done
             ▲          ▲                     │
             └──────────┴──── route ◀─────────┘ fail
```

| Role | Tools | Sees | Why it is separate |
| --- | --- | --- | --- |
| `locate` | `find_files`, `search_code`, `list_dir` | task | Finding files needs search, not editing |
| `inspect` | `read_lines`, `search_code` | task, files, test | Reading needs no write access |
| `edit` | `replace_in_file`, `create_file` | task, notes, edits, test | Applying a change needs no search |
| `[test]` | — | — | **Deterministic. No model call at all** |
| `route` | **none** | task, notes, edits, test, log | The call that decides what happens next holds nothing that can make anything happen |

Two of those are the load-bearing design choices:

- **`route` has no tools.** Deciding and acting are never the same call, so a
  model cannot wander into executing something while it is supposed to be
  choosing. It answers with one word, parsed against a fixed set, with a
  deterministic fallback — a small model writing a sentence instead of a word
  must not end a run.
- **`test` runs no model.** "Run the tests after an edit" needs no
  intelligence. Every edge the model does not get to choose is an LLM call not
  spent and a way the run cannot go wrong.

### 6.12.2 What it costs

Measured on a saturated blackboard (30 notes, 30 files, 400 lines of test
output — far past any real run):

| Role | tools | schema tok | fresh | saturated |
| --- | ---: | ---: | ---: | ---: |
| `locate` | 3 | 166 | 266 | 266 |
| `inspect` | 2 | 133 | 237 | 659 |
| `edit` | 2 | 156 | 233 | 1,278 |
| `route` | 0 | 0 | 101 | 1,145 |

Against the deep-agents loop's **~6,097 tokens on every step**, tool schemas
fall from 5,533 to 133–166 per role. The worst case any role can reach is
~1,281 tokens, which fits the 6,000-TPM pool member that cannot serve the wide
loop at all. A test pins this: if any role's saturated prompt exceeds the
5,400-token usable ceiling, the suite fails.

### 6.12.3 First live run

One task ("`add()` returns the wrong value; fix it"), one run, against the real
pool:

| | baseline (deepagents, recorded r1) | ad-hoc harness |
| --- | ---: | ---: |
| outcome | pass | pass |
| provider calls | 11 | 9 |
| distinct models | 5 | 2 |
| failover bounces | 4 | 1 |
| `tokens_in` | 89,260 | **3,369** |
| served by Gemini | 4 of 6 steps | **none** |

The whole task ran on `llama-3.3-70b` and `gpt-oss-120b` — the Groq models that
[6.8.1](#681-the-overhead-prices-the-groq-half-of-the-pool-out) shows are priced
out of the wide loop by step 4. That is the mechanism working end to end: cheap
enough per call that the cheap half of the pool can actually serve it.

**These two runs are not a comparison.** They are different scenarios, n=1
each, and the baseline's number comes from a previously recorded run. The
controlled version is `python -m evals run --config baseline --config
adhoc-harness --reps 5`, interleaved, on the same scenario
([8.6](08-evaluation-method.md#86-fair-comparison)). Until that exists, the
honest claim is narrow: the harness works end to end, and its per-call cost is
roughly an order of magnitude lower.

### 6.12.4 What this trades away

Stated plainly, because the token win is not free:

- **More turns.** Four roles where the wide loop took one step. On a pool
  limited by requests-per-minute as well as tokens, more calls is a real cost —
  it is why `provider_calls` is a gating metric, not a vanity one.
- **No cross-step memory beyond the blackboard.** A finding that no role writes
  down is gone. The caps in `blackboard.py` are the context budget, and setting
  them too tight loses information the model needed.
- **The pipeline is opinionated.** `locate → inspect → edit → test` fits
  bugfix-shaped work. A refactor or a greenfield feature may want a different
  graph, and today that means editing `loop.py`, not configuration.
- **`route` is the only adaptive edge.** Everything else is fixed. That is what
  makes it cheap and predictable, and also what makes it rigid.

Whether the trade pays is exactly what
[8. Evaluation method](08-evaluation-method.md) exists to answer, and it needs
scenarios past L0 to answer it — a single-file bugfix is the case this
pipeline is shaped for, so it is the case least likely to reveal the limits.

### 6.12.5 A tracing gap this exposed

The trace claimed to record provider-level events, but that only worked because
LangGraph supplies an ambient run context for the provider call to inherit.
Driving `RouterChatModel` directly — no graph — recorded the wrapper and
nothing else, so `provider_calls`, `models_used` and `failover_bounces` all read
zero. An unmeasurable harness cannot be promoted, so `RouterChatModel` gained an
opt-in `provider_config`, defaulting to `None`, which leaves the deep-agents
path byte-identical. See [7.3](07-observability.md#73-the-local-trace).

## 6.13 What the comparison actually showed

n=3, interleaved, one L0 scenario, both configurations drawing from an
identical pool (`llm_router/config.eval.yaml`).

| | baseline | ad-hoc harness |
| --- | ---: | ---: |
| pass | **3 / 3** | 2 / 3 |
| mean provider calls | 18.3 | **13.3** |
| mean failover bounces | 5.7 | **2.8** |
| mean `tokens_in` | 214,814 | **12,388** |
| mean wall time | 44s | **28s** |
| per-rep `tokens_in` | 190,734 / 238,082 / 215,626 | 16,784 / 8,290 / 12,090 |

**The token result is real; the pass-rate result is not.** A 17× gap that held
on every rep is far outside any plausible noise. 3/3 against 2/3 at n=3 is
inside it — [8.6](08-evaluation-method.md#86-fair-comparison) puts the noise
floor near 15 points at ten times this sample. Nothing here promotes the
harness, and nothing here rules it out.

By the letter of the promotion rule the harness is arguably promotable — no task
regressed by more than one trial, success rate is statistically flat, and two
secondary metrics improved materially. That is exactly the situation the rule's
"a draw keeps the simpler configuration" clause exists for. **One L0 scenario
cannot settle this**, which is why scenario authoring, not more reps, is the
blocking item ([11.5](11-eval-status.md#115-what-to-do-next)).

### 6.13.1 The one failure is the interesting part

The failing rep did produce a fix, and its own tests passed:

```diff
-    value = headers.get("retry-after")
+    value = headers.get("Retry-After")
```

The scenario is about **case-insensitive** header lookup. The agent flipped the
case to satisfy the test it could see, and the hidden `fail_to_pass` set — which
checks other casings — failed it. Classified `reasoning`, correctly.

This is the failure mode the withheld-test design exists to catch
([9.3](09-scenarios.md#93-anatomy)), and it is *not* a token-budget problem. It
says something the token numbers cannot: the harness closes its own loop
faithfully against the visible tests, and closing that loop is not the same as
being right. A cheaper agent reaches this failure mode sooner, not later.

Reading the same run's role log shows the second-order cost: the first `inspect`
returned "no finding" and the first `edit` applied nothing, burning a whole
cycle before `route` sent it back to `inspect`, which then succeeded. Roughly a
third of that run's calls bought nothing — the motivation for the variants in
[6.14](#614-architecture-variants-tried).

### 6.13.2 Three bugs the comparison surfaced

None of these were the thing being measured, and all three mattered more than it:

**Two dead models, not one.** Groq 404s on `qwen3-32b` as well as
`llama-4-scout`. Probing every pool member directly found both in one pass —
worth doing instead of discovering them one crash at a time.

**Cheaper agents trip landmines that expensive ones never reach.** The harness
crashed on `qwen3-32b` where the baseline never did, and not because it is more
fragile: a dead model is only reached if a request is small enough to pass the
size filter. `qwen3-32b`'s 6,000-token ceiling meant the deep-agents loop was
*never* routed to it. Shrinking requests un-hid it. Expect any efficiency work
to expose pool members that were previously unreachable, and audit the pool
before reading the results.

**A crash after the work was done.** A model emitted a narrow no-break space in
its finding; printing the run summary raised `UnicodeEncodeError` against the
Windows console codepage. The fix had already been applied and verified, but the
process lost its diagnostics and exited non-zero. Found by noticing that a run
scored `pass` had a `stdout.log` that stopped mid-summary — the kind of thing
only visible if you read the evidence rather than the score.

---

## 6.14 Architecture variants tried

Each variant is selectable by `HARNESS_VARIANT` and has its own eval
configuration, so it is measured rather than argued about
([variants.py](../agent/harness/variants.py)). All runs are on the same L0
scenario, interleaved, identical pool.

| Variant | What it changes | pass | calls | bounces | `tokens_in` |
| --- | --- | ---: | ---: | ---: | ---: |
| `baseline` | The deep-agents loop | 3/3 | 20.0 | 6.5 | 226,854 |
| `v1-pipeline` | The original fixed pipeline | 4/6 | 16.8 | 4.2 | 15,067 |
| `v3-merged` | One `investigate` role replaces locate + inspect | 4/6 | 12.8 | 4.7 | 10,499 |
| `v6-guarded` | v5 + forced summary + no-note guard on edit | 2/3 | **7.3** | **2.0** | **5,756** |
| `v5-lean` | Seeded files + edit retry + two-tool locate | 1/3 | 15.0 | 4.7 | 12,827 |
| `v2-seeded` | Glob the file list, skip `locate` | 0/3 | 15.7 | 4.0 | 13,755 |
| `v7-orchestrated` | Hub and spoke; execution as an agent | 0/3 | 19.7 | 7.3 | 12,543 |

### 6.14.1 The pass column is noise, and I can prove it

`v3-merged` scored **3/3 in one batch and 1/3 in the next** — same
configuration, same scenario, same pool, hours apart. Its true rate across both
is 4/6, identical to `v1`.

So the ordering in that table is not a ranking. An earlier draft of this page
read `v3`'s 3/3 as evidence that merging roles helps and seeding hurts, and
built an argument on it. The replication says that argument had no support.
[8.6](08-evaluation-method.md#86-fair-comparison) predicted exactly this — the
noise floor is around 15 points at ten times this sample — and it is worth
recording that the prediction was borne out rather than quietly deleting the
claim.

**What survives is the cost column.** Token and call counts were stable across
every rep and every batch, and the spread between configurations is far larger
than their variance. `v6-guarded` runs the same task on **7.3 calls and 5,756
tokens against the baseline's 20.0 and 226,854** — a 39× reduction that
replicated everywhere.

### 6.14.2 Every failure is `reasoning`, and that reframes the whole exercise

Across all 21 harness runs, the failure taxonomy
([10.3](10-metrics.md#103-failure-taxonomy)) says:

| class | count |
| --- | ---: |
| `reasoning` | 12 |
| `stopping` | 1 |
| `retrieval` | 0 |
| `tooling` | 0 |

Not one failure was the agent failing to find the code, or failing to express an
edit. Every variant located the file, applied a change, ran the tests — and got
the fix conceptually wrong, almost always the same way: flipping
`headers.get("retry-after")` to `"Retry-After"` instead of making the lookup
case-insensitive ([6.13.1](#6131-the-one-failure-is-the-interesting-part)).

**Architecture cannot fix that.** Routing, role splits, context partitioning and
tool budgets all address `retrieval`, `tooling` and `stopping`. The measured
bottleneck is none of those. This is the conclusion the taxonomy exists to
deliver, and it says the remaining headroom on this task is in model tier and
prompt, not in topology — the harness work bought a 39× cost reduction and has
close to nothing left to give on correctness.

It also means the harness and baseline may be closer than 3/3-vs-4/6 suggests:
both fail the same way, and the baseline has more chances to stumble into the
right answer because it spends 20× the tokens doing it.

### 6.14.3 Observations from the logs

These are mechanisms seen while reading run traces. They are **observations, not
results** — the pass rates above cannot support them, and each would need its
own measurement on a scenario set that can discriminate.

**Seeding removed the wrong thing.** `v2-seeded` hands `inspect` a correct file
list for free and skips a model call, and it was the worst pipeline variant.
Reading its runs, `locate` looks valuable less for the file list it produces
than because searching for the task's identifiers is how the agent works out
what the task is *about*. Seeding delivers the artifact and skips the
engagement. Plausible, unproven, and cheap to test properly later.

**Handoffs lose information.** Anything a role does not write into a capped
blackboard note is gone when it returns. Merging locate and inspect removes one
such boundary. This is the strongest argument for *not* splitting roles further
than the token budget requires — partition context to fit the budget, not for
its own sake.

**Hub and spoke cost the most and passed the least.** `v7` spent the most calls
(19.7) and took the most failover bounces (7.3) of any variant, for 0/3. A model
call per decision is a real cost on a pool this size, and on a task whose shape
the fixed pipeline already matches, the flexibility bought nothing. It should be
re-tested on a scenario the pipeline *cannot* express — a multi-file feature, or
anything needing a replan — because that is the only place its extra cost could
pay for itself.

### 6.14.4 Two bugs the variants exposed

**A role could do all its work and report nothing.** If a role spent every round
on tool calls, the loop ended holding a tool-calling response, whose text
content is empty. The blackboard then got nothing, and the next role acted
blind. In one recorded run the `edit` role — write tools, no finding to act on —
invented a `read_files.py` to explore with, because exploring was the only thing
left to do with the tools it had. Fixed by `force_summary` (spend the last round
with no tools bound, so the role must speak) and `require_note` (refuse to enter
`edit` with nothing to act on).

**Forcing a summary broke the roles it was applied to.** `force_summary` on the
*edit* role stole the round it needed to act: given two rounds with the last one
tool-less, a model would spend the first thinking and then describe a fix it
never applied — sixteen edit calls in one run, no edit. Roles now declare
`reports`, and the forced summary applies only where the role's value is what it
*says* rather than what it *does*. That single distinction took the same
orchestrated task from 40 calls and no fix to 15 calls, fixed and verified.

### 6.14.5 `v7-orchestrated`: hub and spoke

The fixed pipeline can only do bugfix-shaped work in a fixed order. `v7`
replaces it with an orchestrator that chooses the next worker every time:

```
        ┌──────────────► orchestrate ◄──────────────┐
        │              (no tools, sees all)         │
        │        ┌────────┬────┴───┬─────────┐      │
        └── explore    plan      edit     execute ──┘
```

Three changes from the pipeline:

- **Execution is an agent, not a fixed step.** The run can check things other
  than the test suite, and can decide when checking is worth it.
- **The orchestrator holds the widest context** — task, plan, files, notes,
  edits, last command, and the step log — and is consulted after every worker,
  so it can replan when a result surprises it.
- **It still holds no tools.** Deciding and acting stay in different calls; that
  constraint survives the topology change.

This is the opposite bet from the rest of the harness: a model call per
decision, and the biggest prompt in the system, bought in exchange for
flexibility. Two guards proved necessary from watching it run:

- **A `DONE` that no successful run backs is refused once.** An unverified
  "done" is the `stopping` failure class wearing a confident face.
- **Execution verdicts come from the tool's exit code, not the model's summary
  of it.** A model that reports "everything passes" about a failing run would
  otherwise end the task.

And one deterministic edge had to be put back: **after an edit applies, run the
code.** Orchestrators repeatedly chose `EDIT` again instead of verifying, never
reached a check, and exhausted the budget — 40 calls with the fix already
applied and never confirmed. "Check what you just changed" is always the right
next move, so it is not worth a model call.

---

**Previous:** [← 5. Providers and limits](05-providers.md) · **Next:** [7. Observability →](07-observability.md)
