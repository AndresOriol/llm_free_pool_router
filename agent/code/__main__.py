"""CLI: python -m agent.code [workdir] < brief.md

Workdir as an argument, task on stdin, exit at EOF. `python -m agent.explore`
takes the same shape, so an eval configuration or a script swaps one agent for
the other by changing `agent_cmd` and nothing else (evals/agent_config.py).

Environment:
  ROUTER_CONFIG      pool config to load; unset uses llm_router/config.yaml
  AGENT_TRACE_FILE    where to write the run tree; unset writes none
  EVAL_TRACE_FILE    set by the eval runner; the run tree lands beside it
  AGENT_CONTEXT_FLOOR override the input-token floor (default 128,000)
  AGENT_PEERS        comma-separated agents this one may delegate to; unset
                     means `explore`, and an empty value means none
                     (docs/16-agent-protocol.md)
  HARNESS_SHELL=1    give the agent an unrestricted shell -- not contained,
                     so this is the operator's call, never a default
"""

import logging
import os
import sys
from pathlib import Path

from llm_router import AutonomousLLMRouter, load_providers_from_config
from agent.code.session import (CONTEXT_FLOOR, RECURSION_LIMIT, check_floor,
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
    # The router itself comes back too, because a delegated agent must run on
    # the *same* provider objects and so share their cooldown
    # (docs/16-agent-protocol.md#163-why-the-transport-is-local).
    return model.for_context(floor, strict=True), members, router


def main() -> None:
    workdir = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    workdir.mkdir(parents=True, exist_ok=True)

    task = sys.stdin.read().strip() if not sys.stdin.isatty() else ""
    if not task:
        print("Enter the task, then Ctrl-D:", file=sys.stderr)
        task = sys.stdin.read().strip()
    if not task:
        raise SystemExit("No task given.")

    floor = int(os.environ.get("AGENT_CONTEXT_FLOOR") or CONTEXT_FLOOR)
    model, members, router = build(floor)

    shell = os.environ.get("HARNESS_SHELL") == "1"
    if shell:
        logging.warning("HARNESS_SHELL=1: the agent has an unrestricted shell. "
                        "Run this inside a container.")

    # The eval runner names the trace itself, per run, so a configuration
    # cannot set AGENT_TRACE_FILE ahead of time -- it does not yet know the run
    # directory. It exports EVAL_TRACE_FILE instead, pointing at the flat
    # `trace.jsonl` the callback handler writes (agent/runtime/trace.py). The
    # run tree is a second, nested record fetched from LangSmith
    # (agent/code/trace.py), so it takes the directory and not the name:
    # writing a JSON tree to a `.jsonl` path would both lie about the format
    # and overwrite the file every metric is summed over.
    trace_file = os.environ.get("AGENT_TRACE_FILE")
    if not trace_file and os.environ.get("EVAL_TRACE_FILE"):
        trace_file = Path(os.environ["EVAL_TRACE_FILE"]).with_name("trace.json")

    # The task records go beside the run record and never inside the workdir:
    # the agent commits its workdir, and a protocol log committed into the
    # project under review is noise in every diff it produces afterwards.
    transport = peers.build_transport(
        router, model, workdir, floor=floor, members=members,
        recursion_limit=RECURSION_LIMIT,
        record_dir=Path(trace_file).parent / "a2a" if trace_file else None)

    # Hours of wall time with long gaps between calls looks like an idle
    # machine to Windows. Suspending mid-request is what left one run waiting
    # 43 minutes on a socket that had died while it slept.
    with keep_awake():
        final, written = run_session(
            model, task, workdir, floor=floor, members=members,
            allow_shell=shell, transport=transport,
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
