This document is used to annotate ideas of improvement that can be later used by tha coding agent.

## Improving the router

I feel that it would be better to build a more robust router that would led by its configuration to select a model or some models, so you can have more control over what you are using.
Improve the rerouting so that there are less misses when there is a lot of traffic for a model.

### Optional (high complexity)

Create an automatic classifier that selects the best model depending on the task and current availability of the pool. This implementation only makes sense if the previous 2 themes are already implemented. This improvement by itself can be considered an independent project.
## What the deepagents examples do that we do not

From a review of <https://github.com/langchain-ai/deepagents/tree/main/examples>
against our three agents. Two of those examples are systems this repo arrived at
independently, which makes the places they differ worth taking seriously rather
than treating as a matter of taste.

### Split the eval set into train and holdout

`better-harness` is `agent/improve` + `evals` built by the library's authors: an
outer agent edits an inner agent's surfaces, and **a change is kept only if the
combined pass count on a *train* and a *holdout* split improves**.

We have no holdout. `check_issue` replays an issue's signature against whatever
runs exist, and `delegate_fix` gates on staleness rather than on a score. That
is how a harness change overfits the handful of scenarios it was diagnosed from
— and we already know the pass column is noise at these sample sizes
([6.4.2](../06-agent.md#642-the-pass-column-is-noise)), so the risk is not
theoretical. This is the most valuable thing in the examples for us.

Worth deciding first: whether a holdout is affordable at our sample sizes at
all, or whether the honest version is to hold out *scenarios* rather than runs
and accept that a pass is a weak signal either way.

### Declare the editable surfaces

`better-harness` names what an outer agent may edit, as data: a prompt, a tool
file, a skill, a middleware, each loaded either by patching a module attribute
(`package.module:ATTRIBUTE`) or by replacing a file in the workspace for one
eval run.

Our issues carry a free-text `lever` — a path, written by the model, that
nothing checks. A declared surface would make `_staleness` a lookup instead of a
git archaeology pass ([19](../19-improvement-agent.md)), and would let a
delegation be refused for naming something that is not editable.

### Read `ralph_mode` before building more of the long-run harness

Fresh context each iteration, the filesystem and git as the only memory. That is
the shape [long-run-harness.md](long-run-harness.md) describes, already written
down by someone else. Read the two side by side before adding to ours.

### Evaluate `deepagents-cli`, do not assume it fits

`run_non_interactive` handles model resolution, tool registration,
checkpointing, streaming and HITL approval — most of what our three
`__main__.py` do. But it is **not installed and not a declared dependency**, and
model resolution is the pool's whole job here, so the part that would carry the
most weight is the part that fits worst.
