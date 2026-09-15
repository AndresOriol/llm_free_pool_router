"""CLI: python -m agent.explore [workdir] [--research-dir DIR] --task "..."

Same shape as `python -m agent.code` -- workdir as an argument, the task on
`--task` or on stdin -- so the two are interchangeable in a script or an eval
configuration:

    python -m agent.explore  ./project < question.md   # research, writes /research
    python -m agent.code     ./project < brief.md      # build, reads /research

**This command is also how the coding agent delegates research**: it runs this
with `execute` and reads what is printed below (docs/16-delegation.md).

`--research-dir` is where the notes go, relative to the workdir; it defaults to
`research`. Name one per investigation to keep them apart, and name an existing
one to continue it:

    python -m agent.explore ./project --research-dir research/cv-spain < question.md
    python -m agent.explore ./project --research-dir research/cv-spain < follow-up.md

Environment:
  ROUTER_CONFIG        pool config to load; unset uses llm_router/config.yaml
  AGENT_TRACE_FILE     where to write the run tree; unset writes none
  EVAL_TRACE_FILE      set by the eval runner; the run tree lands beside it
  AGENT_CONTEXT_FLOOR  override the input-token floor (default 128,000)
  TAVILY_API_KEY_1..N  the search pool; at least one is required

What the agent is and how it is built is [agent.py](agent.py).
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from agent.explore.agent import CONTEXT_FLOOR, RESEARCH_DIR, connect, run
from agent.utils import cli
from agent.utils.awake import keep_awake

cli.setup()


def main() -> None:
    flags = argparse.ArgumentParser(add_help=False)
    flags.add_argument("--research-dir", default=RESEARCH_DIR)
    known, rest = flags.parse_known_args(sys.argv[1:])
    research_dir = known.research_dir
    workdir, task = cli.parse(
        rest, prog="python -m agent.explore",
        workdir_help="where to write the notes; unset means this directory",
        task_help="what to research; unset reads it from stdin",
        prompt="Enter what to research, then Ctrl-D:")
    workdir.mkdir(parents=True, exist_ok=True)
    if not task:
        raise SystemExit("No task given.")

    floor = int(os.environ.get("AGENT_CONTEXT_FLOOR") or CONTEXT_FLOOR)
    model, members, search_pool = connect(floor)

    trace_file = os.environ.get("AGENT_TRACE_FILE")
    if not trace_file and os.environ.get("EVAL_TRACE_FILE"):
        trace_file = Path(os.environ["EVAL_TRACE_FILE"]).with_name("trace.json")

    # Which notes were already there, so a second run does not report the
    # first one's files as its own findings.
    before = _notes(workdir, research_dir)

    # Hours of wall time with long gaps between calls looks like an idle
    # machine to Windows. Suspending mid-request is what left one run waiting
    # 43 minutes on a socket that had died while it slept.
    with keep_awake():
        final, written = run(
            model, task, workdir, search_pool, floor=floor, members=members,
            research_dir=research_dir,
            trace_path=Path(trace_file) if trace_file else None)

    _summary(final, written, workdir, before, research_dir)
    sys.exit(0)


def _notes(workdir: Path, research_dir: str = RESEARCH_DIR) -> dict:
    """Every note in the research directory, by path, with its size."""
    directory = workdir / research_dir
    if not directory.is_dir():
        return {}
    return {p: p.stat().st_size for p in sorted(directory.rglob("*.md"))}


def _summary(final, written, workdir: Path, before=None,
             research_dir: str = RESEARCH_DIR) -> None:
    """The run's account of itself on stdout, final message first.

    The final message is the output of this command, unclipped: a caller that
    ran it reads this and nothing else. The notes below it are the deliverable.
    """
    messages = (final or {}).get("messages") or []
    if messages:
        text = getattr(messages[-1], "content", "")
        if isinstance(text, list):  # content blocks
            text = " ".join(str(b.get("text", "")) for b in text
                            if isinstance(b, dict))
        print(f"\n{str(text).strip()}")
    print(f"\n=== DONE after {len(messages)} message(s) ===")

    # "none" is the loudest thing this can print: the run spent quota and left
    # nothing behind.
    notes = _notes(workdir, research_dir)
    fresh = [p for p, size in notes.items() if (before or {}).get(p) != size]
    print(f"\nnotes written by this run: {len(fresh) or 'none'}")
    for note in fresh:
        print(f"  {note.relative_to(workdir).as_posix()} "
              f"({notes[note]:,} bytes)")
    older = [p for p in notes if p not in fresh]
    if older:
        print(f"\nalready in /{research_dir} before this run "
              f"(not this run's findings): "
              + ", ".join(p.relative_to(workdir).as_posix() for p in older))

    if written:
        print(f"\nrun record: {written}")


if __name__ == "__main__":
    main()
