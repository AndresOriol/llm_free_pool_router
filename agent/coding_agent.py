"""A deep-agents coding agent that runs on the free-tier router pool.

Holds a single RouterChatModel as its model, so every step of the agent loop
routes through the pool's failover: if one free account hits its usage limit
mid-task, the next step just runs on another account/model and the task keeps
going. Operates on the real local filesystem via deepagents' FilesystemBackend.

Run:
    python -m agent.coding_agent [workdir]
"""

import sys
import logging
from pathlib import Path

# Allow `python agent/coding_agent.py` as well as `python -m agent.coding_agent`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deepagents import create_deep_agent
from langchain_core.messages import AIMessage, ToolMessage

from llm_router import load_providers_from_config, AutonomousLLMRouter
from agent.router_chat_model import RouterChatModel
from agent.restricted_backend import RestrictedShellBackend

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
# The pool's routing/cooldown logs are the interesting ones; quiet the rest.
logging.getLogger("LLMRouter").setLevel(logging.INFO)

INSTRUCTIONS = """You are an autonomous coding agent working on a real local \
filesystem rooted at the working directory. Use the file tools (ls, read_file, \
glob, grep) to understand the project before changing it, and write_file / \
edit_file to make changes. Break non-trivial work into a todo list with \
write_todos and work through it. Prefer the simplest change that works; match \
the surrounding code's style. The working directory root is `/`: write files \
at paths like `/calc.py` and `/test_calc.py` (do NOT invent paths like \
`/home/user`). Verify your work with the execute tool, which runs from the \
project root: run the tests with execute(command="python -m pytest") -- it \
discovers tests in the current directory. Only python and pytest may be run \
(no pip, no shell). A task is not done until its tests pass. When you finish, \
briefly summarize what you did."""

_RUN_CONFIG = {"recursion_limit": 150}


def build_agent(workdir: Path):
    providers = load_providers_from_config()
    if not providers:
        raise SystemExit("No providers loaded. Set your keys in llm_router/.env.")

    router = AutonomousLLMRouter(providers)
    # Give the failover budget room to walk the whole pool in one step: on free
    # tiers several small-TPM models may reject a large request before a
    # higher-limit account (e.g. Gemini) accepts it.
    model = RouterChatModel(router=router, max_retries=len(providers) + 3)
    # File tools stay jailed under workdir; execute() is limited to running the
    # project's own tests (python/pytest), so the agent can close its own loop
    # without an unrestricted host shell. See agent/restricted_backend.py.
    backend = RestrictedShellBackend(root_dir=str(workdir))
    return create_deep_agent(model=model, system_prompt=INSTRUCTIONS, backend=backend)


def _render(message) -> None:
    """Print a single new message from the agent's stream, concisely."""
    if isinstance(message, AIMessage):
        for call in message.tool_calls or []:
            print(f"  [tool] {call['name']}({call.get('args', {})})")
        if message.content:
            print(f"\n{message.content}")
    elif isinstance(message, ToolMessage):
        text = str(message.content)
        preview = text if len(text) <= 200 else text[:200] + " ..."
        print(f"    -> {preview}")


def main() -> None:
    workdir = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    workdir.mkdir(parents=True, exist_ok=True)

    agent = build_agent(workdir)

    if not sys.stdin.isatty():
        task = sys.stdin.read()
        history = [{"role": "user", "content": task}]
        final_state = None
        seen = 0
        for state in agent.stream({"messages": history}, stream_mode="values", config=_RUN_CONFIG):
            final_state = state
            messages = state["messages"]
            while seen < len(messages):
                _render(messages[seen])
                seen += 1
        return

    print(f"Coding agent ready. Working dir: {workdir}")
    print("Enter a task, or 'exit' to quit.")

    history = []
    while True:
        try:
            user = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user:
            continue
        if user.lower() in {"exit", "quit"}:
            break

        history.append({"role": "user", "content": user})
        final_state = None
        seen = len(history)
        for state in agent.stream({"messages": history}, stream_mode="values", config=_RUN_CONFIG):
            final_state = state
            messages = state["messages"]
            while seen < len(messages):
                _render(messages[seen])
                seen += 1
        history = final_state["messages"]


if __name__ == "__main__":
    main()
