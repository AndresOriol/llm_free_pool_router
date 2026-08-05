[← Wiki index](README.md)

# 6. The coding agent

*What the agent can do, what it is not allowed to do, and which of its
behaviours are ours versus inherited from the library.*

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
then the library's own ~110-line base agent prompt (task loop discipline,
terseness, "don't stop until genuinely blocked"). Because ours sits first, it
wins on conflict — but the base prompt is always present underneath.

## 6.4 What the library gives us, and what we left at defaults

The agent gets deepagents' built-in tools: `write_todos`, the filesystem set
(`ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`), `execute`, and
`task` for delegating to a subagent.

Everything downstream of `create_deep_agent` is **library defaults**. No
`skills=`, no `subagents=`, no `permissions=`, no custom `middleware=`. That is
worth stating plainly because it means the entire context-management surface is
currently untuned:

- Because the model is a custom `RouterChatModel` instance rather than a
  `provider:model` string, deepagents can never resolve a harness profile for
  it. No provider-specific prompt tuning or tool-description overrides ever
  apply — **every model the router serves gets the identical generic prompt**.
- With no model profile, summarization falls back to its conservative default:
  trigger at 170,000 tokens, keep the last 6 messages. That threshold is almost
  certainly wrong here — several Groq free-tier models cap out *far* below
  170k, so a step can hit a hard context overflow on a small model before
  summarization ever decides to compact.

The full teardown of deepagents' middleware stack, summarization algorithm,
backends and subagent isolation — read from the installed source — is in
[.claude/reports/deepagents-architecture.md](../.claude/reports/deepagents-architecture.md),
along with a ranked list of the knobs worth pulling. It is a reference
document, not a plan; anything acted on from it becomes a measured change like
any other ([8. Evaluation method](08-evaluation-method.md)).

## 6.5 Loop budget

The run config sets `recursion_limit: 150` — the ceiling on agent loop
iterations. Hitting it is a real observed failure mode, classified as
`stopping` in the failure taxonomy ([10.3](10-metrics.md#103-failure-taxonomy)),
and it is one of the things a harness change might legitimately try to improve.

## 6.6 What failover looks like in practice

Expect a single agent step to walk several small-TPM Groq models before a
higher-capacity account accepts the request. That is the design working, not a
malfunction — but it is also why the pool gets drained fast, and why
`failover_bounces` is tracked as a first-class metric
([10.2](10-metrics.md#102-automatic-metrics)).

Free tiers signal limits inconsistently — Groq uses HTTP 429 *and* 413 for
tokens-per-minute — and both are treated as transient
([4.3](04-failover.md#43-classifying-a-failure)).

---

**Previous:** [← 5. Providers and limits](05-providers.md) · **Next:** [7. Observability →](07-observability.md)
