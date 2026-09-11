"""CLI: python -m agent.code [workdir] --task "..."   (or: < brief.md)

Workdir as an argument, the task on `--task` or on stdin, exit at EOF.
`python -m agent.explore` takes the same shape, so an eval configuration or a
script swaps one agent for the other by changing `agent_cmd` and nothing else
(evals/agent_config.py).

**This command is also how another agent delegates to this one.** There is no
protocol: a caller runs it with `execute` and reads the summary below
(docs/16-delegation.md).

Environment:
  ROUTER_CONFIG      pool config to load; unset uses llm_router/config.yaml
  AGENT_TRACE_FILE    where to write the run tree; unset writes none
  EVAL_TRACE_FILE    set by the eval runner; the run tree lands beside it
  AGENT_CONTEXT_FLOOR override the input-token floor (default 128,000)
  AGENT_STEP_BUDGET  override the superstep budget (default 400); the last
                     few are reserved so a stopped run can still commit
  AGENT_PEERS        comma-separated agents this one may run; unset means
                     `explore`, and an empty value means none
                     (docs/16-delegation.md)
  AGENT_INVARIANT_GUARD=0, AGENT_WRITE_ACCOUNT=0
                     leave that section out of the prompt (agent.py)
  HARNESS_SHELL=1    give the agent an unrestricted shell -- not contained,
                     so this is the operator's call, never a default

What the agent is and how it is built is [agent.py](agent.py).
"""

import logging
import os
import sys
from pathlib import Path

from langchain_core.messages import AIMessage

from agent.code.agent import CONTEXT_FLOOR, RECURSION_LIMIT, connect, run
from agent.runtime import cli, gitstate
from agent.runtime.awake import keep_awake

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


def main() -> None:
    workdir, task = cli.parse(
        sys.argv[1:], prog="python -m agent.code",
        workdir_help="the project to work on; unset means the current directory",
        task_help="what to do; unset reads it from stdin",
        prompt="Enter the task, then Ctrl-D:")
    workdir.mkdir(parents=True, exist_ok=True)
    if not task:
        raise SystemExit("No task given.")

    floor = int(os.environ.get("AGENT_CONTEXT_FLOOR") or CONTEXT_FLOOR)
    budget = int(os.environ.get("AGENT_STEP_BUDGET") or RECURSION_LIMIT)
    model, members = connect(floor)

    shell = os.environ.get("HARNESS_SHELL") == "1"
    if shell:
        logging.warning("HARNESS_SHELL=1: the agent has an unrestricted shell. "
                        "Run this inside a container.")

    # The eval runner names the trace itself, per run, so a configuration
    # cannot set AGENT_TRACE_FILE ahead of time -- it does not yet know the run
    # directory. It exports EVAL_TRACE_FILE instead, pointing at the flat
    # `trace.jsonl` the callback handler writes (agent/runtime/trace.py). The
    # run tree is a second, nested record fetched from LangSmith
    # (agent/runtime/run_tree.py), so it takes the directory and not the name:
    # writing a JSON tree to a `.jsonl` path would both lie about the format
    # and overwrite the file every metric is summed over.
    trace_file = os.environ.get("AGENT_TRACE_FILE")
    if not trace_file and os.environ.get("EVAL_TRACE_FILE"):
        trace_file = Path(os.environ["EVAL_TRACE_FILE"]).with_name("trace.json")

    # The head this run starts from, so the summary can say what actually moved
    # rather than leaving a reader to trust the closing message
    # (agent/runtime/gitstate.py).
    before = gitstate.head(workdir)

    # Hours of wall time with long gaps between calls looks like an idle
    # machine to Windows. Suspending mid-request is what left one run waiting
    # 43 minutes on a socket that had died while it slept.
    with keep_awake():
        final, written = run(
            model, task, workdir, config={"recursion_limit": budget},
            floor=floor, members=members, allow_shell=shell,
            trace_path=Path(trace_file) if trace_file else None)

    _summary(final, written, gitstate.state(workdir, before))
    # Exit 0 for any clean end. Whether the work was any good is the hidden
    # tests' verdict, not this process's exit code -- exiting non-zero on an
    # orderly stop made the eval runner record it as `crash`, which means the
    # opposite (docs/08-evaluation-method.md#85-the-run-lifecycle).
    sys.exit(0)


def _text(message) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, list):  # content blocks
        content = " ".join(str(b.get("text", "")) for b in content
                           if isinstance(b, dict))
    return str(content).strip()


def final_message(final) -> str:
    """What the session said last, in full.

    The last *message* is not always the last thing the model said: a run
    stopped mid-turn ends on a tool call whose content is empty, and printing
    that reported nothing at all about a session that had done real work. Walk
    back to the last thing the *model* said -- a tool's own output is not this
    session's account of itself.
    """
    said = [m for m in (final or {}).get("messages") or []
            if isinstance(m, AIMessage)]
    return next((t for t in map(_text, reversed(said)) if t), "")


def _summary(final, written, state=None) -> None:
    """The run's account of itself on stdout, final message first.

    **The final message is the output of this command**, unclipped, because
    another agent may have run it and this is the answer it gets back
    (docs/16-delegation.md). Everything below it is the mechanical detail a
    human wants and a caller can ignore.
    """
    messages = (final or {}).get("messages") or []
    text = final_message(final)
    if text:
        print(f"\n{text}")

    # Spending the step budget is an ordinary end, not a crash, but it is not
    # the same end as finishing -- whoever reads this has to know the session
    # was stopped rather than done (agent.py, section 6).
    how = ("STOPPED (step budget spent)"
           if (final or {}).get("step_budget_spent") else "DONE")
    print(f"\n=== {how} after {len(messages)} message(s) ===")

    # What the repository says, after what the session says about itself. A
    # caller reading this back from a delegation gets the verdict either way;
    # the prose above is not evidence and this is
    # (docs/19-improvement-agent.md#199-what-the-first-live-pass-showed).
    verdict = gitstate.render(state or {})
    if verdict:
        print(f"\n{verdict}")

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
