"""CLI: python -m agent.improve [project] < what-to-look-into.md

Same shape as `python -m agent.code` and `python -m agent.explore` -- project as
an argument, task on stdin, exit at EOF -- so the three are interchangeable in a
script or an eval configuration. The difference is what the argument means: for
the other two it is a project to work on, and here it is a project whose
*recorded runs* are to be read. In practice that is this repository.

With nothing on stdin it runs the standing pass: work the ledger. That is the
job on most days, and an unattended schedule should not have to carry a prompt
file around to say so.

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
  EVAL_SCENARIOS     the scenario repository, for building a drafted scenario;
                     unset looks for `agent_evals` beside this one
  HARNESS_SHELL=1    give the *delegated* coding agent an unrestricted shell.
                     This agent never gets one: it runs no programs but `git`.
"""

import logging
import os
import sys
from pathlib import Path

from langchain_core.messages import AIMessage

from llm_router import AutonomousLLMRouter, load_providers_from_config
from agent.code.session import CONTEXT_FLOOR, RECURSION_LIMIT, check_floor
from agent.improve.issues import IssueStore
from agent.improve.session import (NothingToImproveOn, check_records,
                                   run_session)
from agent.protocol import peers
from agent.runtime.awake import keep_awake
from agent.runtime.chat_model import RouterChatModel

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")
logging.getLogger("LLMRouter").setLevel(logging.INFO)

# Models emit characters the Windows console codepage cannot encode, and an
# unencodable character in the summary raised UnicodeEncodeError *after* the
# work was done -- losing the diagnostics on a run that had actually passed.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # not a reconfigurable stream
        pass

STANDING_PASS = (
    "Work the ledger.\n\n"
    "1. Check every issue that is not closed against the runs recorded since it "
    "was delegated, and close or reopen it on what you find.\n"
    "2. Then look for a failure that recurs across the runs and is not in the "
    "ledger yet. Diagnose it against the source, open it with a signature, and "
    "delegate the fix.\n\n"
    "Finish one issue end to end rather than opening several."
)


def build(floor: int):
    providers = load_providers_from_config(os.environ.get("ROUTER_CONFIG") or None)
    if not providers:
        raise SystemExit("No providers loaded. Set your keys in llm_router/.env.")
    router = AutonomousLLMRouter(providers)
    members = check_floor(router, floor)
    model = RouterChatModel(router=router, max_retries=len(providers) + 3)
    # The router comes back too: a delegated coding session must run on the
    # *same* provider objects and so share this pass's cooldown
    # (docs/16-agent-protocol.md#163-why-the-transport-is-local).
    return model.for_context(floor, strict=True), members, router


def main() -> None:
    workdir = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    if not workdir.is_dir():
        raise SystemExit(f"{workdir} is not a directory.")

    task = sys.stdin.read().strip() if not sys.stdin.isatty() else ""
    if not task:
        task = STANDING_PASS
        print("No task on stdin; running the standing pass over the ledger.",
              file=sys.stderr)

    # Before the pool is touched: a pass with nothing recorded to read can never
    # do its job, and the message names the command that would fix that.
    try:
        seen = check_records(workdir)
    except NothingToImproveOn as exc:
        raise SystemExit(str(exc))

    floor = int(os.environ.get("AGENT_CONTEXT_FLOOR") or CONTEXT_FLOOR)
    budget = int(os.environ.get("AGENT_STEP_BUDGET") or RECURSION_LIMIT)
    model, members, router = build(floor)

    trace_file = os.environ.get("AGENT_TRACE_FILE")
    if not trace_file and os.environ.get("EVAL_TRACE_FILE"):
        trace_file = Path(os.environ["EVAL_TRACE_FILE"]).with_name("trace.json")

    # Two peers: `code` for a fix in this repository, and `scenarios` -- the
    # same coding agent bound to the eval repo -- for building a scenario the
    # pass has drafted. `scenarios` registers only if that repo is really there.
    # IMPROVE_FIX=0 takes it away, which leaves a diagnose-only pass -- the arm
    # to compare against when asking whether the delegation is worth what it
    # spends (docs/19-improvement-agent.md).
    wanted = () if os.environ.get("IMPROVE_FIX") == "0" else ("code", "scenarios")
    transport = peers.build_transport(
        router, model, workdir, floor=floor, members=members,
        recursion_limit=RECURSION_LIMIT, peers=wanted,
        allow_shell=os.environ.get("HARNESS_SHELL") == "1",
        record_dir=Path(trace_file).parent / "a2a" if trace_file else None)

    print(f"Reading {seen} recorded run(s) under {workdir}.", file=sys.stderr)

    # Hours of wall time with long gaps between calls looks like an idle
    # machine to Windows, and this agent has the longest gaps of the three:
    # one `run_evals` call can be an hour of somebody else's runs.
    with keep_awake():
        final, written = run_session(
            model, task, workdir, config={"recursion_limit": budget},
            floor=floor, members=members, transport=transport,
            trace_path=Path(trace_file) if trace_file else None)

    _summary(final, written, workdir)
    sys.exit(0)


def _text(message) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, list):  # content blocks
        content = " ".join(str(b.get("text", "")) for b in content
                           if isinstance(b, dict))
    return str(content).strip()


def _summary(final, written, workdir: Path) -> None:
    messages = (final or {}).get("messages") or []
    print(f"\n=== DONE after {len(messages)} message(s) ===")
    said = [m for m in messages if isinstance(m, AIMessage)]
    text = next((t for t in map(_text, reversed(said)) if t), "")
    if text:
        print(f"\n{text[:2000]}")

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
