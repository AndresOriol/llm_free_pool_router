import sys
import logging
from pathlib import Path

# Allow running either as `python -m llm_router.main` or `python llm_router/main.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm_router import load_providers_from_config, AutonomousLLMRouter

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
    messages = [
        {"role": "system", "content": "You are a concise scientific assistant."},
        {"role": "user", "content": "Explica brevemente qué es el entrelazamiento cuántico."},
    ]

    logger.info("Starting autonomous generation loop...")
    answer = router.generate(messages)
    print("\n --- FINAL ANSWER ---")
    print(answer)


if __name__ == "__main__":
    main()
