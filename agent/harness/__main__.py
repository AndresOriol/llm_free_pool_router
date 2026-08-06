"""CLI: python -m agent.harness [workdir] < brief.md

Same contract as `agent.coding_agent` -- workdir argument, task on stdin -- so
an eval configuration can swap one for the other by setting `agent_cmd` and
nothing else.
"""

import logging
import os
import sys
from pathlib import Path

from llm_router import AutonomousLLMRouter, load_providers_from_config
from agent.harness import variants
from agent.harness.loop import DEFAULT_TEST_CMD, MAX_CYCLES, solve
from agent.harness.tools import make_tools
from agent.restricted_backend import RestrictedShellBackend
from agent.router_chat_model import RouterChatModel
from agent.trace import tracer_from_env

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
    backend = RestrictedShellBackend(root_dir=str(workdir))
    return model, backend, make_tools(backend)


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

    variant = variants.get(os.environ.get("HARNESS_VARIANT"))
    logging.info(f"Variant: {variant.name} -- {variant.note}")

    bb, stats, outcome = solve(model, backend, toolset, task, config=config,
                               test_cmd=os.environ.get("HARNESS_TEST_CMD")
                               or DEFAULT_TEST_CMD,
                               max_cycles=variant.max_cycles_hint or MAX_CYCLES,
                               variant=variant)

    print(f"\n=== {outcome.upper()} after {bb.cycles} cycle(s) [{variant.name}] ===")
    for line in bb.log:
        print(f"  {line}")
    avg = stats.prompt_tokens // stats.calls if stats.calls else 0
    print(f"\nmodel calls: {stats.calls} | ~prompt tokens: {stats.prompt_tokens} "
          f"| ~avg/call: {avg}")
    for role, s in sorted(stats.by_role.items()):
        print(f"  {role:<8} {s['calls']:>2} calls  ~{s['tokens'] // max(s['calls'], 1)} tok/call")

    sys.exit(0 if outcome == "pass" else 1)


if __name__ == "__main__":
    main()
