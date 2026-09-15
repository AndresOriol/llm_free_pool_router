"""How every agent command starts: its logging, its console, and its two arguments.

One function, shared by the three `__main__.py` entry points, so they cannot
drift. They were already the same shape by convention -- workdir as an argument,
task on stdin -- and that convention became load-bearing when delegation moved
to the command line: one agent runs another with `execute`, and `execute` gives
the child no stdin and refuses redirection outright
([backend.py](backend.py)). So the task has to be sayable on the command line,
and `--task` is the whole of what this adds.

Stdin still works and is still the right way to hand over a long brief from a
shell, because a file redirect is a shell's job and there is no shell in the
jail. Both together are refused rather than silently preferring one: an operator
who piped a file *and* passed `--task` meant something, and guessing which is
how a run gets the wrong instructions and nobody notices.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def setup() -> None:
    """The console every agent command wants, before it prints anything.

    Was copied into all three `__main__.py` byte for byte, comment included.
    Nothing here is agent-specific: the router's progress is the one library
    log worth seeing at INFO, and the stream reconfiguration is about Windows,
    not about any agent.

    Models emit characters the Windows console codepage cannot encode, and an
    unencodable character in a summary raised UnicodeEncodeError *after* the
    work was done -- losing the diagnostics on a run that had actually passed.
    """
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s - %(levelname)s - %(message)s")
    logging.getLogger("LLMRouter").setLevel(logging.INFO)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # not a reconfigurable stream
            pass


def text(message) -> str:
    """A message's text, whatever shape its content arrived in.

    A provider may return content as a string or as a list of block dicts, and
    a summary that printed `[{'type': 'text', ...}]` would be unreadable.
    """
    content = getattr(message, "content", "")
    if isinstance(content, list):  # content blocks
        content = " ".join(str(b.get("text", "")) for b in content
                           if isinstance(b, dict))
    return str(content).strip()


def parse(argv, *, prog: str, workdir_help: str, task_help: str,
          prompt: str = "") -> tuple:
    """(workdir, task) for one agent command. `task` may be ''.

    `prompt` is what to print when there is a terminal on stdin and no task; an
    empty `prompt` means this command has a sensible default and should not ask
    (`python -m agent.improve` runs its standing pass).
    """
    parser = argparse.ArgumentParser(
        prog=prog, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("workdir", nargs="?", default=None, help=workdir_help)
    parser.add_argument("--task", default="", help=task_help)
    args = parser.parse_args(list(argv))

    piped = sys.stdin.read().strip() if not sys.stdin.isatty() else ""
    if piped and args.task.strip():
        raise SystemExit("Give the task either on stdin or with --task, not "
                         "both; there is no way to tell which one you meant.")

    task = args.task.strip() or piped
    if not task and prompt and sys.stdin.isatty():
        print(prompt, file=sys.stderr)
        task = sys.stdin.read().strip()

    workdir = Path(args.workdir).resolve() if args.workdir else Path.cwd()
    return workdir, task
