"""CLI: python -m agent.deep [workdir] < brief.md

Same shape as `python -m agent.harness` on purpose -- workdir as an argument,
task on stdin, exit at EOF -- so an eval configuration can swap one for the
other by changing `agent_cmd` and nothing else (evals/agent_config.py).

Environment:
  ROUTER_CONFIG      alternative pool config (evals use config.eval.yaml)
  DEEP_TRACE_FILE    where to write the run tree; unset writes none
  DEEP_CONTEXT_FLOOR override the input-token floor (default 128,000)
  HARNESS_SHELL=1    give the agent an unrestricted shell -- not contained,
                     so this is the operator's call, never a default
"""

import logging
import os
import sys
from pathlib import Path

from llm_router import AutonomousLLMRouter, load_providers_from_config
from agent.deep.session import CONTEXT_FLOOR, check_floor, run_session
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
    providers = load_providers_from_config(os.environ.get("ROUTER_CONFIG") or None)
    if not providers:
        raise SystemExit("No providers loaded. Set your keys in llm_router/.env.")
    router = AutonomousLLMRouter(providers)
    members = check_floor(router, floor)
    # A step must be able to walk the whole pool once before giving up: with a
    # hard floor the eligible set is smaller than the pool, and an unlucky
    # ordering of benched accounts must not end the run.
    model = RouterChatModel(router=router, max_retries=len(providers) + 3)
    return model.for_context(floor, strict=True), members


def main() -> None:
    workdir = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    workdir.mkdir(parents=True, exist_ok=True)

    task = sys.stdin.read().strip() if not sys.stdin.isatty() else ""
    if not task:
        print("Enter the task, then Ctrl-D:", file=sys.stderr)
        task = sys.stdin.read().strip()
    if not task:
        raise SystemExit("No task given.")

    floor = int(os.environ.get("DEEP_CONTEXT_FLOOR") or CONTEXT_FLOOR)
    model, members = build(floor)

    shell = os.environ.get("HARNESS_SHELL") == "1"
    if shell:
        logging.warning("HARNESS_SHELL=1: the agent has an unrestricted shell. "
                        "Run this inside a container.")

    trace_file = os.environ.get("DEEP_TRACE_FILE")
    final, written = run_session(
        model, task, workdir, floor=floor, members=members, allow_shell=shell,
        trace_path=Path(trace_file) if trace_file else None)

    _summary(final, written)
    # Exit 0 for any clean end. Whether the work was any good is the hidden
    # tests' verdict, not this process's exit code -- exiting non-zero on an
    # orderly stop made the eval runner record it as `crash`, which means the
    # opposite (docs/08-evaluation-method.md#85-the-run-lifecycle).
    sys.exit(0)


def _summary(final, written) -> None:
    messages = (final or {}).get("messages") or []
    print(f"\n=== DONE after {len(messages)} message(s) ===")
    if messages:
        last = messages[-1]
        text = getattr(last, "content", "")
        if isinstance(text, list):  # content blocks
            text = " ".join(str(b.get("text", "")) for b in text
                            if isinstance(b, dict))
        print(f"\n{str(text)[:2000]}")
    todos = (final or {}).get("todos") or []
    if todos:
        print(f"\ntodos: {len(todos)}")
        for todo in todos:
            if isinstance(todo, dict):
                print(f"  [{todo.get('status', '?')}] {todo.get('content', '')}")
    if written:
        print(f"\nrun record: {written}")


if __name__ == "__main__":
    main()
