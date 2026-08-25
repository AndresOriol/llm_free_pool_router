"""One deepagents session over one workdir, on the pool.

This is the second agent architecture, built to be measured against the first
rather than to replace it (docs/12-development-harness.md#127). The narrow-role
graph in `agent/harness/` splits work so every call fits the pool's *narrowest*
member; this one keeps a conversation and makes it fit by staying on the pool's
*widest* members and letting the SDK summarize when the history grows.

Almost nothing here is agent design. `create_deep_agent` already assembles the
todo list, the filesystem tools, the subagent `task` tool and summarization; the
`execute` tool switches on by itself because `RestrictedShellBackend` satisfies
`SandboxBackendProtocol`. What this file owns is the three seams that are ours:

1. **The model is the pool.** `resolve_model` returns a `BaseChatModel`
   unchanged, and `RouterChatModel` is one, so the router drops in with no
   adapter (docs/04-failover.md#45-the-failover-loop).
2. **A hard context floor.** Groq's members top out at 8,000 input tokens and
   100,000 tokens *per day*; one full-context request would spend an account's
   entire daily budget. A conversational harness cannot be trimmed to fit them,
   so it refuses to route below the floor and waits for a wide member instead.
   Groq stays in the pool for work that fits it.
3. **The record is a run tree**, fetched from LangSmith (agent/deep/trace.py).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from langchain_core.messages import HumanMessage
from langchain_core.tracers.context import collect_runs

from agent.deep import trace as run_trace

logger = logging.getLogger("harness.deep")

# The floor, in input tokens. Sized to admit both Gemini families (250,000) and
# Gemma (128,000) while excluding every Groq member (8,000). It is a property of
# the *pool*, not a guess: raising it past 128,000 would drop Gemma and leave
# only the request-scarce Gemini accounts (llm_router/config.yaml).
CONTEXT_FLOOR = 128_000

# Supersteps, not agent turns. A backstop against a loop that never settles --
# the real budget on a free pool is the daily request quota, which the run hits
# long before this.
RECURSION_LIMIT = 120

SYSTEM_PROMPT = """\
You are working on the project rooted at `/`, which is the only directory you \
can reach. Paths are relative to it.

Before you claim a task is done, run the project's tests yourself and read the \
output. A change you have not executed is not finished, and a test you did not \
read the result of did not pass.

Change only what the task asks for. Do not refactor code you were not asked to \
touch, and do not add features nobody requested.

If you get stuck on the same failure three times, stop and write down what you \
tried and what you think is wrong, rather than trying a fourth variation.\
"""


def build_agent(workdir: Path, model, allow_shell: bool = False):
    """The compiled deep agent over a jailed backend."""
    from deepagents import create_deep_agent

    from agent.runtime.backend import RestrictedShellBackend

    backend = RestrictedShellBackend(root_dir=str(workdir), allow_git=True,
                                     allow_shell=allow_shell)
    return create_deep_agent(model=model, backend=backend,
                             system_prompt=SYSTEM_PROMPT)


def check_floor(router, floor: int = CONTEXT_FLOOR) -> None:
    """Fail before the run rather than during it.

    With a hard floor and no member wide enough, every step would walk its
    retry budget and die on "all providers exhausted" -- an error that describes
    a rate limit, not a pool that never could have served this agent.
    """
    wide = [p for p in router.providers
            if p.max_input_tokens is None or p.max_input_tokens >= floor]
    if not wide:
        raise SystemExit(
            f"No provider in the pool holds {floor:,} input tokens, so this "
            f"harness cannot run on it. Widen the floor, or use "
            f"`python -m agent.harness`, which is built for narrow members.")
    logger.info(f"{len(wide)} of {len(router.providers)} providers meet the "
                f"{floor:,}-token floor.")


def _root_run_id(runs) -> Optional[str]:
    """The id of the tree's root, given every run the collector saw.

    Not `runs[0]`. The collector appends in *completion* order, so the first
    entry is the innermost LLM call, and fetching it returns a one-span tree
    that looks like a working trace until you count the spans. The root is the
    run with no parent; `trace_id` is the fallback, since every run in a tree
    carries the root's id there.
    """
    if not runs:
        return None
    root = next((r for r in runs if getattr(r, "parent_run_id", None) is None), None)
    if root is not None:
        return str(root.id)
    trace_id = getattr(runs[0], "trace_id", None)
    return str(trace_id) if trace_id else str(runs[0].id)


def run_session(model, task: str, workdir: Path, config=None,
                allow_shell: bool = False,
                trace_path: Optional[Path] = None) -> tuple:
    """Run one session. Returns (final_state, trace_written)."""
    config = dict(config or {})
    config.setdefault("recursion_limit", RECURSION_LIMIT)
    workdir = Path(workdir)

    agent = build_agent(workdir, model, allow_shell=allow_shell)

    # `collect_runs` learns the root run id from the same callbacks LangSmith's
    # tracer uses, so the fetch afterwards knows what to ask for without a
    # network round trip during the run.
    with collect_runs() as collected:
        final = agent.invoke({"messages": [HumanMessage(task)]}, config)

    root_id = _root_run_id(collected.traced_runs)

    written = None
    if trace_path is not None:
        tree = run_trace.fetch_tree(root_id) if root_id else None
        written = run_trace.write(trace_path, tree, meta={
            "root_run_id": root_id,
            "workdir": str(workdir),
            "harness": "deepagents",
            "context_floor": CONTEXT_FLOOR,
            "tracing_enabled": run_trace.tracing_enabled(),
        })
        if written:
            logger.info(f"Wrote the run record to {written}")

    return final, written
