"""CLI: python -m agent.explore [workdir] < brief.md

Same shape as `python -m agent.code` -- workdir as an argument, task on stdin,
exit at EOF -- so the two are interchangeable in a script or an eval
configuration, and so the obvious workflow needs no glue:

    python -m agent.explore  ./project < question.md   # research, writes /research
    python -m agent.code     ./project < brief.md      # build, reads /research

Environment:
  ROUTER_CONFIG      pool config to load; unset uses llm_router/config.yaml
  AGENT_TRACE_FILE    where to write the run tree; unset writes none
  EVAL_TRACE_FILE    set by the eval runner; the run tree lands beside it
  AGENT_CONTEXT_FLOOR override the input-token floor (default 128,000)

There is no HARNESS_SHELL here. The coding agent has one because it has to run
the tests it writes; this one runs nothing, and an escape hatch nobody needs is
just a hole (agent/explore/session.py).
"""

import logging
import os
import sys
from pathlib import Path

from llm_router import AutonomousLLMRouter, load_providers_from_config
from agent.code.session import CONTEXT_FLOOR, check_floor
from agent.explore.session import RESEARCH_DIR, check_search, run_session
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


def build(floor: int):
    """(model, eligible member count, web pools). Raises before the run."""
    providers = load_providers_from_config(os.environ.get("ROUTER_CONFIG") or None)
    if not providers:
        raise SystemExit("No providers loaded. Set your keys in llm_router/.env.")
    router = AutonomousLLMRouter(providers)

    # Both checks up front, and the search one first: a pool that cannot reach
    # the web can never do this job, and finding that out after twenty minutes
    # of reading files is the expensive way to learn it.
    web = check_search(router)
    members = check_floor(router, floor)

    model = RouterChatModel(router=router, max_retries=len(providers) + 3)
    return model.for_context(floor, strict=True), members, web


def main() -> None:
    workdir = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    workdir.mkdir(parents=True, exist_ok=True)

    task = sys.stdin.read().strip() if not sys.stdin.isatty() else ""
    if not task:
        print("Enter what to research, then Ctrl-D:", file=sys.stderr)
        task = sys.stdin.read().strip()
    if not task:
        raise SystemExit("No task given.")

    floor = int(os.environ.get("AGENT_CONTEXT_FLOOR") or CONTEXT_FLOOR)
    model, members, web = build(floor)

    trace_file = os.environ.get("AGENT_TRACE_FILE")
    if not trace_file and os.environ.get("EVAL_TRACE_FILE"):
        trace_file = Path(os.environ["EVAL_TRACE_FILE"]).with_name("trace.json")

    # Hours of wall time with long gaps between calls looks like an idle
    # machine to Windows. Suspending mid-request is what left one run waiting
    # 43 minutes on a socket that had died while it slept.
    with keep_awake():
        final, written = run_session(
            model, task, workdir, web, floor=floor, members=members,
            trace_path=Path(trace_file) if trace_file else None)

    _summary(final, written, workdir)
    sys.exit(0)


def _summary(final, written, workdir: Path) -> None:
    messages = (final or {}).get("messages") or []
    print(f"\n=== DONE after {len(messages)} message(s) ===")
    if messages:
        last = messages[-1]
        text = getattr(last, "content", "")
        if isinstance(text, list):  # content blocks
            text = " ".join(str(b.get("text", "")) for b in text
                            if isinstance(b, dict))
        print(f"\n{str(text)[:2000]}")

    # The notes are the deliverable, so the summary names them rather than
    # leaving the operator to go looking. An empty list is the loudest thing
    # this can print: the run talked to itself and left nothing behind.
    notes = sorted((workdir / RESEARCH_DIR).rglob("*.md"))
    print(f"\nnotes in /{RESEARCH_DIR}: {len(notes)}")
    for note in notes:
        print(f"  {note.relative_to(workdir).as_posix()} "
              f"({note.stat().st_size:,} bytes)")

    if written:
        print(f"\nrun record: {written}")


if __name__ == "__main__":
    main()
