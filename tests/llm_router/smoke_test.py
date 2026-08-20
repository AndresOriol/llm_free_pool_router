import sys
import logging
from pathlib import Path

# Allow running either as `python -m tests.llm_router.smoke_test` or
# `python tests/llm_router/smoke_test.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from llm_router import load_providers_from_config, AutonomousLLMRouter
from agent.runtime.chat_model import RouterChatModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    force=True,
)
logger = logging.getLogger("Main")


def main():
    pool = load_providers_from_config()
    if not pool:
        logger.error("No providers loaded. Verify your .env file and config.yaml.")
        return

    router = AutonomousLLMRouter(providers=pool)
    model = RouterChatModel(router=router)
    messages = [
        {"role": "system", "content": "You are a concise scientific assistant."},
        {"role": "user", "content": "Explica brevemente qué es el entrelazamiento cuántico."},
    ]

    logger.info("Starting autonomous generation loop...")
    answer = model.invoke(messages).content
    print("\n --- FINAL ANSWER ---")
    print(answer)


if __name__ == "__main__":
    main()
