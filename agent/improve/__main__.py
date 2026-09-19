"""CLI: python -m agent.improve [project] --task "..."   (or: < what-to-do.md)

Same shape as `python -m agent.code` and `python -m agent.explore` -- project as
an argument, the task on `--task` or on stdin, exit at EOF -- so the three are
interchangeable in a script or an eval configuration. The difference is what the
argument means: for the other two it is a project to work on, and here it is a
project whose *recorded runs* are to be read. In practice that is this
repository.

With no task at all it runs the standing pass, `prompts/standing_pass.md`: work
the ledger. That is the job on most days, and an unattended schedule should not
have to carry a prompt file around to say so.

    python -m agent.improve .                     # work the ledger
    echo "..." | python -m agent.improve .        # look into something specific

Environment:
  ROUTER_CONFIG      pool config to load; unset uses llm_router/config.yaml
  AGENT_TRACE_FILE   where to write this pass's own run tree; unset writes none
  EVAL_TRACE_FILE    set by the eval runner; the run tree lands beside it
  AGENT_CONTEXT_FLOOR override the input-token floor (default 128,000)
  AGENT_STEP_BUDGET  override the superstep budget (default 400)
  IMPROVE_RECORDS    extra directories of recorded runs, comma-separated;
                     `evals/results/runs` is always read
  IMPROVE_EVAL_TIMEOUT  ceiling in seconds on one `run_evals` call (default 3600)
  IMPROVE_FIX=0      diagnose only -- no peers, so nothing can be delegated
  AGENT_DELEGATE_TIMEOUT  ceiling in seconds on one delegated session
                     (default 4 hours)
  EVAL_SCENARIOS     the scenario repository, for building a drafted scenario;
                     unset looks for `agent_evals` beside this one

What the agent is and how it is built is [agent.py](agent.py).
"""

import logging
import os
import sys
from pathlib import Path

from langchain_core.messages import AIMessage

from agent import delegation
from agent.improve.agent import (CONTEXT_FLOOR, RECURSION_LIMIT,
                                 NothingToImproveOn, check_records, connect,
                                 prompt, run)
from agent.improve.issues import IssueStore
from agent.utils import cli
from agent.utils.awake import keep_awake

cli.setup()


def main() -> None:
    workdir, task = cli.parse(
        sys.argv[1:], prog="python -m agent.improve",
        workdir_help=("the project whose recorded runs are to be read; unset "
                      "means this directory"),
        task_help="what to look into; unset runs the standing pass")
    if not workdir.is_dir():
        raise SystemExit(f"{workdir} is not a directory.")
    if not task:
        task = prompt("standing_pass.md", {})
        print("No task given; running the standing pass over the ledger.",
              file=sys.stderr)

    # Before the pool is touched: a pass with nothing recorded to read can never
    # do its job, and the message names the command that would fix that.
    try:
        seen = check_records(workdir)
    except NothingToImproveOn as exc:
        raise SystemExit(str(exc))

    floor = int(os.environ.get("AGENT_CONTEXT_FLOOR") or CONTEXT_FLOOR)
    budget = int(os.environ.get("AGENT_STEP_BUDGET") or RECURSION_LIMIT)
    model, members = connect(floor)

    trace_file = os.environ.get("AGENT_TRACE_FILE")
    if not trace_file and os.environ.get("EVAL_TRACE_FILE"):
        trace_file = Path(os.environ["EVAL_TRACE_FILE"]).with_name("trace.json")

    # Two peers: `code` for a fix in this repository, and `scenarios` -- the
    # same coding agent bound to the eval repo -- for building a scenario the
    # pass has drafted. `scenarios` is offered only if that repo is really
    # there. IMPROVE_FIX=0 takes both away, which leaves a diagnose-only pass --
    # the arm to compare against when asking whether the delegation is worth
    # what it spends (docs/agents/improve.md).
    wanted = () if os.environ.get("IMPROVE_FIX") == "0" else ("code", "scenarios")
    peers = delegation.available(wanted)

    print(f"Reading {seen} recorded run(s) under {workdir}.", file=sys.stderr)

    # Hours of wall time with long gaps between calls looks like an idle
    # machine to Windows, and this agent has the longest gaps of the three:
    # one `run_evals` call can be an hour of somebody else's runs.
    with keep_awake():
        final, written = run(
            model, task, workdir, config={"recursion_limit": budget},
            floor=floor, members=members, peers=peers,
            trace_path=Path(trace_file) if trace_file else None)

    _summary(final, written, workdir)
    sys.exit(0)



def _summary(final, written, workdir: Path) -> None:
    """The pass's account of itself on stdout, final message first.

    **The final message is the output of this command**, unclipped, because a
    caller may have run it from a shell and this is the answer it gets back
    (docs/agents/delegation.md).
    """
    messages = (final or {}).get("messages") or []
    said = [m for m in messages if isinstance(m, AIMessage)]
    text = next((t for t in map(cli.text, reversed(said)) if t), "")
    if text:
        print(f"\n{text}")
    print(f"\n=== DONE after {len(messages)} message(s) ===")

    # The ledger is the deliverable, so the summary prints it rather than
    # leaving the operator to go looking. An unchanged ledger is the loudest
    # thing this can report: the pass read traces, spent quota, and left the
    # project exactly as it found it.
    print("\n--- the ledger ---")
    print(IssueStore(workdir).summary())

    if written:
        print(f"\nrun record: {written}")


if __name__ == "__main__":
    main()
