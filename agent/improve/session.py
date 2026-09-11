"""One improvement pass over one project, on the pool.

The same loop, the same jail and the same pool as the other two agents; what
differs is what it is pointed at and what it is allowed to do about it.

- **The workdir is this repository**, not a scenario checkout. The runs it reads
  are under `evals/results/runs/`, the source it diagnoses against is
  `agent/`, and the ledger it writes is `evals/results/issues/`. Point it at a
  directory with none of those and it has nothing to work on, which
  `check_records` says up front rather than after twenty minutes.
- **It cannot write a file.** [readonly.py](readonly.py) refuses the filesystem
  write tools, so the only way a change reaches the harness is `delegate_fix` ->
  `agent/code`. That separation is the agent's one structural claim.
- **It can spend real quota**, through `run_evals`. It is the only agent here
  that launches other agents as a subprocess rather than in-process, because the
  eval runner materialises a pinned worktree per configuration and that is the
  point of it ([evals/agent_config.py](../../evals/agent_config.py)).

`delegate_fix` runs `python -m agent.code` and waits, the same way `run_evals`
runs the eval runner. There is no protocol between them: the coding agent is a
command, and what comes back is what it printed
([16. Delegation](../../docs/16-delegation.md)).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Sequence

from langchain_core.messages import HumanMessage
from langchain_core.tracers.context import collect_runs

from agent.runtime import run_tree
from agent.runtime.pool import CONTEXT_FLOOR
from agent.improve import prompt, records, repo, tools as improve_tools
from agent.improve.issues import IssueStore
from agent.improve.readonly import ReadOnlyMiddleware
from agent.runtime.trace import tracer_from_env

logger = logging.getLogger("harness.improve")

# `git` and nothing else. The coding agent gets `python` and `pytest` because it
# has to run the tests it writes; this one runs no tests and writes no code, and
# `python` would be a way around every boundary above -- it could launch a batch
# without `run_evals`'s ceiling on it, or edit a file the middleware refuses.
# What it genuinely needs a program for is reading history: which commit the
# coding agent just made, and what moved in it.
ALLOWED_PROGRAMS = ("git",)

# Supersteps: a budget, not a loop guard, sized as the coding agent's
# (agent/code/agent.py).
RECURSION_LIMIT = 400


class NothingToImproveOn(RuntimeError):
    """The workdir holds no recorded runs and no ledger."""


def check_records(workdir: Path) -> int:
    """How many recorded runs this pass can see. Raises if there are none.

    Up front, like the explorer's search-pool check: an improvement pass with no
    traces to read can never do its job, and finding that out after the model
    has spent ten steps looking is the expensive way to learn it.
    """
    found = records.discover(records.default_roots(Path(workdir)))
    if not found:
        roots = ", ".join(str(r) for r in records.default_roots(Path(workdir)))
        raise NothingToImproveOn(
            f"No recorded runs under: {roots}. Record some with "
            f"`python -m evals run --config code --scenario <tag>`, or point "
            f"{records.ROOTS_ENV} at where this project's runs are kept.")
    live = sum(1 for r in found if r.kind == "live")
    logger.info(f"{len(found)} recorded run(s) to read "
                f"({len(found) - live} eval, {live} live)")
    return len(found)


def ledger_section(workdir: Path) -> str:
    """The open issues, as a prompt section.

    In the prompt rather than left to a first tool call, and for the same reason
    the list of agents it can run is: it is the one thing this agent must know
    before it decides what to do, it changes once per pass, and charging it on
    every step of a session that would have fetched it anyway is the worse
    trade ([delegation.prompt_section](../delegation.py)).
    """
    issues = IssueStore(workdir).list()
    if not issues:
        return ("### The ledger\n\nThe ledger is empty. Nothing has been "
                "diagnosed yet, so this pass starts from the traces.")
    lines = ["### The ledger", "",
             "What is already known. Read it before opening a trace — an issue "
             "that closed and came back outranks anything new.", ""]
    lines += [f"- {issue.row()}" for issue in issues]
    return "\n".join(lines)


def build_agent(workdir: Path, model, *, floor: int = CONTEXT_FLOOR,
                members: int = 0, peers: Optional[Sequence[str]] = None,
                extra_middleware: Optional[Sequence] = None):
    """The compiled improvement agent over a jailed, write-refusing backend."""
    from deepagents import create_deep_agent

    from agent.runtime.shell import ShellAllowListMiddleware
    from agent.runtime.backend import RestrictedShellBackend

    workdir = Path(workdir)
    backend = RestrictedShellBackend(root_dir=str(workdir),
                                     allowed_programs=(), allow_git=True,
                                     allow_shell=False)

    tools = list(improve_tools.make_tools(workdir, peers).values())
    system_prompt = prompt.build(
        floor, members=members, programs=ALLOWED_PROGRAMS,
        extra_sections=[ledger_section(workdir)])

    middleware = [ReadOnlyMiddleware(),
                  ShellAllowListMiddleware(ALLOWED_PROGRAMS)]
    middleware += list(extra_middleware or [])

    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        backend=backend,
        middleware=middleware,
    )


def run_session(model, task: str, workdir: Path, config=None,
                floor: int = CONTEXT_FLOOR, members: int = 0,
                peers: Optional[Sequence[str]] = None,
                trace_path: Optional[Path] = None) -> tuple:
    """Run one improvement pass. Returns (final_state, trace_written)."""
    config = dict(config or {})
    config.setdefault("recursion_limit", RECURSION_LIMIT)
    workdir = Path(workdir)

    jsonl = tracer_from_env()
    if jsonl is not None:
        config["callbacks"] = list(config.get("callbacks") or []) + [jsonl]

    agent = build_agent(workdir, model, floor=floor, members=members,
                        peers=peers)

    # A delegated fix moves the checkout onto `improve/<issue-id>`, and nobody
    # is watching to move it back. Whatever branch the pass started on is the
    # one an eval batch, the next pass and a human all find afterwards
    # ([repo.restored](repo.py)). The fix branches are kept; only the checkout
    # is put back.
    with repo.restored(workdir), collect_runs() as collected:
        final = agent.invoke({"messages": [HumanMessage(task)]}, config)

    written = run_tree.record(collected.traced_runs, trace_path, {
        "workdir": str(workdir),
        "harness": "improve",
        "context_floor": floor,
        "eligible_providers": members,
    })
    return final, written
