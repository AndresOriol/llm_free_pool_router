"""A deep-agents coding agent that runs on the free-tier router pool.

Holds a single RouterChatModel as its model, so every step of the agent loop
routes through the pool's failover: if one free account hits its usage limit
mid-task, the next step just runs on another account/model and the task keeps
going. Operates on the real local filesystem via deepagents' FilesystemBackend.

Run:
    python -m agent.coding_agent [workdir]
"""

import logging
import sys
from pathlib import Path

from deepagents import create_deep_agent
from langchain_core.messages import AIMessage, ToolMessage

from llm_router import load_providers_from_config, AutonomousLLMRouter
from agent.router_chat_model import RouterChatModel
from agent.restricted_backend import RestrictedShellBackend

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
# The pool's routing/cooldown logs are the interesting ones; quiet the rest.
logging.getLogger("LLMRouter").setLevel(logging.INFO)


_RUN_CONFIG = {"recursion_limit": 150}

def load_agent_instructions(workdir: Path): 
    """Load CLAUDE.md and AGENTS.md files from the working directory."""

    # Important: The order of the files is important.
    agent_instruction_files = ["CLAUDE.md", "AGENTS.md"]
    
    for instruction_file in agent_instruction_files:
        instruction_path = workdir / instruction_file
        if instruction_path.is_file():
            try:
                return instruction_path.read_text(encoding="utf-8")
            except Exception as e:
                logging.warning(f"Error reading {instruction_file}: {e}")

    return ""


def build_agent(workdir: Path):
    providers = load_providers_from_config()
    if not providers:
        raise SystemExit("No providers loaded. Set your keys in llm_router/.env.")

    router = AutonomousLLMRouter(providers)
    model = RouterChatModel(router=router, max_retries=len(providers) + 3)
    backend = RestrictedShellBackend(root_dir=str(workdir))

    system_prompt = load_agent_instructions(workdir)

    return create_deep_agent(model=model, system_prompt=system_prompt, backend=backend)


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
