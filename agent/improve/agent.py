"""The improvement agent, assembled: model, prompt, tools, and the agent they make.

This file is the whole harness. Read top to bottom:

1. **Settings** -- the one program it may run, the step budget, and the tools
   it is refused.
2. **Text** -- every word the model reads is a Markdown file: `prompts/` beside
   this one for the job, `tool_descriptions/` for what each tool is for, and
   `agent/runtime/prompts/` for what every agent is told about where it runs.
   Each is a template, and `{name}` is filled from `template_values()`.
3. **Model** -- the pool, held to a context floor (`connect`).
4. **What it knows before it starts** -- the recorded runs, and the ledger.
5. **The boundary** -- `ReadOnlyMiddleware`: a write comes back as a refusal.
6. **The agent** -- `build_agent()`.
7. **A run** -- `run()`.

What the tools *do* is [tools.py](tools.py), over the evidence
([records.py](records.py)), the ledger ([issues.py](issues.py)), git
([repo.py](repo.py)) and scenario drafts ([scenarios.py](scenarios.py)). What a
tool returns is written there, next to the code that computes it. The reasons
for all of it are in
[19. The improvement agent](../../docs/19-improvement-agent.md).

## How context reaches the model

Every call carries:

- the system prompt: `prompts/system.md`, with the open ledger filled in once,
  when the pass starts;
- one schema per tool: its own seven -- six when no `code` peer is reachable,
  because `delegate_fix` would only refuse -- each described by
  `tool_descriptions/<name>.md`, then the framework's file tools, `execute`,
  `write_todos` and `task`, as deepagents writes them;
- the conversation so far. The framework summarizes it when it grows too long.

What outlives the conversation is the ledger in `evals/results/issues/`, and the
branches the coding agent commits its fixes to.

## What differs from the other agents

- **The workdir is this repository**, not a scenario checkout. The runs it
  reads are under `evals/results/runs/`, the source it diagnoses against is
  `agent/`, and the ledger it writes is `evals/results/issues/`.
- **It cannot write a file.** The only way a change reaches the harness is
  `delegate_fix`, which runs `python -m agent.code`
  ([16. Delegation](../../docs/16-delegation.md)). That separation is the
  agent's one structural claim.
- **It can spend real quota**, through `run_evals`, which launches the eval
  runner: it materialises a pinned worktree per configuration, and that is the
  point of it ([evals/agent_config.py](../../evals/agent_config.py)).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Sequence

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tracers.context import collect_runs

from agent.improve import records, repo
from agent.improve.issues import IssueStore
from agent.improve.tools import make_tools
from agent.runtime import run_tree
from agent.runtime.pool import CONTEXT_FLOOR, connect  # noqa: F401 - section 3
from agent.runtime.prompts import fill, shared_values
from agent.runtime.shell import ShellAllowListMiddleware
from agent.runtime.trace import tracer_from_env

logger = logging.getLogger("harness.improve")

HERE = Path(__file__).parent

# --- 1. Settings --------------------------------------------------------------

# `git` and nothing else. The coding agent gets `python` and `pytest` because it
# has to run the tests it writes; this one runs no tests and writes no code, and
# `python` would be a way around every boundary here -- it could launch a batch
# without `run_evals`'s ceiling on it, or edit a file the middleware refuses.
# What it genuinely needs a program for is reading history: which commit the
# coding agent just made, and what moved in it.
ALLOWED_PROGRAMS = ("git",)

# Supersteps: a budget, not a loop guard, sized as the coding agent's
# (agent/code/agent.py).
RECURSION_LIMIT = 400

# The names deep agents give the tools that change a file. Listed rather than
# discovered, so a tool added upstream is refused only once someone has looked
# at it -- the opposite default would silently start refusing work.
WRITE_TOOLS = ("write_file", "edit_file", "str_replace", "apply_patch")


# --- 2. Text --------------------------------------------------------------------

def prompt(name: str, values: dict) -> str:
    """One file from `prompts/`, filled."""
    return fill(HERE / "prompts" / name, values)


def template_values(floor: int = CONTEXT_FLOOR, members: int = 0,
                    ledger: str = "") -> dict:
    """What every `{placeholder}` in `prompts/system.md` is filled with.

    Tool descriptions have no placeholders; `tools.describe` reads them.
    """
    return {
        # Shared with the other agents: where they run, not what they do.
        **shared_values(floor, members, ALLOWED_PROGRAMS),
        "ledger_section": ledger,
    }


def system_prompt(values: dict) -> str:
    """`prompts/system.md`, filled."""
    return re.sub(r"\n{3,}", "\n\n", prompt("system.md", values)).strip() + "\n"


# --- 3. Model -------------------------------------------------------------------
#
# The model is the pool: `connect(floor)` (agent/runtime/pool.py) returns a
# RouterChatModel that routes only to members holding `floor` input tokens, and
# how many there are, which the prompt states.


# --- 4. What it knows before it starts ------------------------------------------

class NothingToImproveOn(RuntimeError):
    """The workdir holds no recorded runs and no ledger."""


def check_records(workdir: Path) -> int:
    """How many recorded runs this pass can see. Raises if there are none.

    Up front, like the explorer's search-pool check: a pass with no traces to
    read can never do its job, and finding that out after the model has spent
    ten steps looking is the expensive way to learn it.
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

    In the prompt rather than left to a first tool call: it is the one thing
    this agent must know before it decides what to do, it changes once per
    pass, and charging it on every step of a session that would have fetched it
    anyway is the worse trade.
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


# --- 5. The boundary ------------------------------------------------------------

class ReadOnlyMiddleware(AgentMiddleware):
    """Reject a filesystem write, as a message. Everything else passes.

    `create_deep_agent` installs `write_file` and `edit_file` over the backend,
    so without this the rule lives only in the prompt -- and a prompt is a
    request. A refusal the model can read and correct from
    (`prompts/write_refused.md`) costs one step; an exception costs the run.
    The ledger needs no hole in it: `write_issue` writes through Python, not
    through the filesystem tools.

    Known gap: the `general-purpose` sub-agent the framework adds for `task`
    gets the framework's middleware only, so this does not reach it.
    """

    def _refusal(self, request) -> Optional[ToolMessage]:
        call = request.tool_call
        name = call.get("name")
        if name not in WRITE_TOOLS:
            return None
        path = (call.get("args") or {}).get("file_path", "")
        logger.warning(f"Refused a write to {path!r} via {name}")
        return ToolMessage(content=prompt("write_refused.md", {"tool": name}),
                           name=name, tool_call_id=call["id"], status="error")

    def wrap_tool_call(self, request, handler: Callable[[Any], Any]) -> Any:
        refusal = self._refusal(request)
        return refusal if refusal is not None else handler(request)

    async def awrap_tool_call(self, request,
                              handler: Callable[[Any], Awaitable[Any]]) -> Any:
        refusal = self._refusal(request)
        return refusal if refusal is not None else await handler(request)


# --- 6. The agent ---------------------------------------------------------------

def build_agent(workdir: Path, model, *, floor: int = CONTEXT_FLOOR,
                members: int = 0, peers: Optional[Sequence[str]] = None,
                extra_middleware: Optional[Sequence] = None):
    """The improvement agent over a jailed, write-refusing workdir.

    `peers` names the agents it may run: `code` for a fix, `scenarios` for
    building a drafted scenario. They become tools, not a prompt paragraph.
    """
    from deepagents import create_deep_agent

    from agent.runtime.backend import RestrictedShellBackend

    workdir = Path(workdir)
    values = template_values(floor, members, ledger=ledger_section(workdir))

    middleware = [ReadOnlyMiddleware(),
                  ShellAllowListMiddleware(ALLOWED_PROGRAMS)]
    middleware += list(extra_middleware or [])

    return create_deep_agent(
        model=model,
        tools=list(make_tools(workdir, peers).values()),
        system_prompt=system_prompt(values),
        # Where the file tools and `execute` work: this repository, jailed,
        # running `git` alone.
        backend=RestrictedShellBackend(root_dir=str(workdir),
                                       allowed_programs=(), allow_git=True,
                                       allow_shell=False),
        middleware=middleware,
    )


# --- 7. A run -------------------------------------------------------------------

def run(model, task: str, workdir: Path, config=None,
        floor: int = CONTEXT_FLOOR, members: int = 0,
        peers: Optional[Sequence[str]] = None,
        trace_path: Optional[Path] = None) -> tuple:
    """One improvement pass. Returns (final_state, run record written or None)."""
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
