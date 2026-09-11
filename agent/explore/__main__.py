"""CLI: python -m agent.explore [workdir] --task "..."   (or: < question.md)

Same shape as `python -m agent.code` -- workdir as an argument, the task on
`--task` or on stdin, exit at EOF -- so the two are interchangeable in a script
or an eval configuration, and so the obvious workflow needs no glue:

    python -m agent.explore  ./project < question.md   # research, writes /research
    python -m agent.code     ./project < brief.md      # build, reads /research

**This command is also how the coding agent delegates research.** There is no
protocol: it runs this with `execute` and reads what is printed below
(docs/16-delegation.md).

Environment:
  ROUTER_CONFIG      pool config to load; unset uses llm_router/config.yaml
  AGENT_TRACE_FILE    where to write the run tree; unset writes none
  EVAL_TRACE_FILE    set by the eval runner; the run tree lands beside it
  AGENT_CONTEXT_FLOOR override the input-token floor (default 128,000)
  TAVILY_API_KEY_1..N  the search pool; at least one is required

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
from agent.explore.session import RESEARCH_DIR, check_pool, run_session
from agent.runtime import cli
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
    """(model, eligible member count, Tavily pool). Raises before the run.

    Two pools, and they are unrelated: the model pool serves the conversation
    and the Tavily pool serves the searching. Neither can substitute for the
    other, so both are checked here.
    """
    from llm_router import TavilyPoolRouter

    providers = load_providers_from_config(os.environ.get("ROUTER_CONFIG") or None)
    if not providers:
        raise SystemExit("No providers loaded. Set your keys in llm_router/.env.")
    router = AutonomousLLMRouter(providers)

    # Both checks up front, and the search one first: an agent that cannot reach
    # the web can never do this job, and finding that out after twenty minutes
    # of reading files is the expensive way to learn it.
    search = TavilyPoolRouter.from_env()
    check_pool(search)
    members = check_floor(router, floor)

    model = RouterChatModel(router=router, max_retries=len(providers) + 3)
    return model.for_context(floor, strict=True), members, search


def main() -> None:
    workdir, task = cli.parse(
        sys.argv[1:], prog="python -m agent.explore",
        workdir_help="where to write the notes; unset means this directory",
        task_help="what to research; unset reads it from stdin",
        prompt="Enter what to research, then Ctrl-D:")
    workdir.mkdir(parents=True, exist_ok=True)
    if not task:
        raise SystemExit("No task given.")

    floor = int(os.environ.get("AGENT_CONTEXT_FLOOR") or CONTEXT_FLOOR)
    model, members, search = build(floor)

    trace_file = os.environ.get("AGENT_TRACE_FILE")
    if not trace_file and os.environ.get("EVAL_TRACE_FILE"):
        trace_file = Path(os.environ["EVAL_TRACE_FILE"]).with_name("trace.json")

    # Which notes were already there. Without this a second run reports the
    # first one's files among its own findings, and a caller -- a person, or
    # the coding agent that ran this command -- reads a stale note believing it
    # answers the new question.
    before = _notes(workdir)

    # Hours of wall time with long gaps between calls looks like an idle
    # machine to Windows. Suspending mid-request is what left one run waiting
    # 43 minutes on a socket that had died while it slept.
    with keep_awake():
        final, written = run_session(
            model, task, workdir, search, floor=floor, members=members,
            trace_path=Path(trace_file) if trace_file else None)

    _summary(final, written, workdir, before)
    sys.exit(0)


def _notes(workdir: Path) -> dict:
    """Every note in the research directory, by path, with its size."""
    directory = workdir / RESEARCH_DIR
    if not directory.is_dir():
        return {}
    return {p: p.stat().st_size for p in sorted(directory.rglob("*.md"))}


def _summary(final, written, workdir: Path, before=None) -> None:
    """The run's account of itself on stdout, final message first.

    **The final message is the output of this command**, unclipped, because the
    coding agent may have run it and this is the answer it gets back
    (docs/16-delegation.md). The notes below it are the deliverable.
    """
    messages = (final or {}).get("messages") or []
    if messages:
        text = getattr(messages[-1], "content", "")
        if isinstance(text, list):  # content blocks
            text = " ".join(str(b.get("text", "")) for b in text
                            if isinstance(b, dict))
        print(f"\n{str(text).strip()}")
    print(f"\n=== DONE after {len(messages)} message(s) ===")

    # The notes are the deliverable, so the summary names them rather than
    # leaving the reader to go looking, and says plainly which ones *this* run
    # wrote. "none" is the loudest thing this can print: the run talked to
    # itself, spent quota, and left nothing behind.
    notes = _notes(workdir)
    fresh = [p for p, size in notes.items() if (before or {}).get(p) != size]
    print(f"\nnotes written by this run: {len(fresh) or 'none'}")
    for note in fresh:
        print(f"  {note.relative_to(workdir).as_posix()} "
              f"({notes[note]:,} bytes)")
    older = [p for p in notes if p not in fresh]
    if older:
        print(f"\nalready in /{RESEARCH_DIR} before this run "
              f"(not this run's findings): "
              + ", ".join(p.relative_to(workdir).as_posix() for p in older))

    if written:
        print(f"\nrun record: {written}")


if __name__ == "__main__":
    main()
