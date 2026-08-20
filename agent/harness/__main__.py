"""CLI: python -m agent.harness [workdir] < brief.md

Workdir as an argument, task on stdin. An eval configuration launches this and
nothing else (evals/agent_config.py).
"""

import logging
import os
import sys
from pathlib import Path

from llm_router import AutonomousLLMRouter, load_providers_from_config
from agent.harness.session import MAX_STEPS, run_session
from agent.runtime.backend import RestrictedShellBackend
from agent.runtime.chat_model import RouterChatModel
from agent.runtime.tools import make_tools
from agent.runtime.trace import tracer_from_env

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logging.getLogger("LLMRouter").setLevel(logging.INFO)

# Models emit characters the Windows console codepage cannot encode (a narrow
# no-break space was enough), and an unencodable character in the run summary
# raised UnicodeEncodeError *after* the work was done -- losing the diagnostics
# and exiting non-zero on a run that had actually passed.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # not a reconfigurable stream
        pass


def build(workdir: Path, config=None):
    providers = load_providers_from_config(os.environ.get("ROUTER_CONFIG") or None)
    if not providers:
        raise SystemExit("No providers loaded. Set your keys in llm_router/.env.")
    router = AutonomousLLMRouter(providers)
    # provider_config: this harness drives the model directly, with no LangGraph
    # run for the provider call to inherit, so the trace would otherwise record
    # only the wrapper and report zero provider calls.
    model = RouterChatModel(router=router, max_retries=len(providers) + 3,
                            provider_config=config or None)
    # Off by default. The Executor node is more capable with a real shell, but
    # this process is not contained, so widening the blast radius is the
    # operator's decision to make rather than a default to inherit
    # (docs/design/long-run-harness.md#42-bash-for-the-executor).
    shell = os.environ.get("HARNESS_SHELL") == "1"
    backend = RestrictedShellBackend(root_dir=str(workdir), allow_git=True,
                                     allow_shell=shell)
    if shell:
        logging.warning("HARNESS_SHELL=1: the Executor has an unrestricted shell. "
                        "Run this inside a container.")
    return model, backend, make_tools(backend, shell=shell)


def main() -> None:
    workdir = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    workdir.mkdir(parents=True, exist_ok=True)

    task = sys.stdin.read().strip() if not sys.stdin.isatty() else ""
    if not task:
        print("Enter the task, then Ctrl-D:", file=sys.stderr)
        task = sys.stdin.read().strip()
    if not task:
        raise SystemExit("No task given.")

    config = {}
    tracer = tracer_from_env()
    if tracer is not None:
        config["callbacks"] = [tracer]
        logging.info(f"Writing eval trace to {tracer.path}")

    model, backend, toolset = build(workdir, config)

    bb, stats, outcome, steps = run_session(
        model, backend, toolset, task, workdir, config=config,
        max_steps=int(os.environ.get("HARNESS_MAX_STEPS") or MAX_STEPS))
    _summary(outcome, bb, stats, len(steps))
    # Exit 0 for any *clean* end -- done, gave up, or out of budget. All three
    # wrote a rationale and left the branch reviewable, which is what R3 asks of
    # a session that gets stuck. Exiting non-zero made the eval runner record an
    # orderly "exhausted" as `crash`, which is the one outcome that means the
    # opposite: that nothing was reported at all. Whether the work was any good
    # is the hidden tests' verdict, not this.
    sys.exit(0)


def _summary(outcome, bb, stats, steps) -> None:
    print(f"\n=== {outcome.upper()} after {steps} step(s) ===")
    for line in bb.log:
        print(f"  {line}")
    avg = stats.prompt_tokens // stats.calls if stats.calls else 0
    print(f"\nmodel calls: {stats.calls} | ~prompt tokens: {stats.prompt_tokens} "
          f"| ~avg/call: {avg}")
    for role, s in sorted(stats.by_role.items()):
        print(f"  {role:<10} {s['calls']:>2} calls  ~{s['tokens'] // max(s['calls'], 1)} tok/call")


if __name__ == "__main__":
    main()
