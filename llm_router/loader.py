import os
import logging
from pathlib import Path
from typing import List

import yaml
from dotenv import load_dotenv

from .base_provider import LLMProvider
from .providers import OpenAICompatibleProvider, GeminiProvider

logger = logging.getLogger("LLMRouter")

_PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = _PACKAGE_DIR / "config.yaml"

_PROVIDER_TYPES = {
    "openai_compatible": OpenAICompatibleProvider,
    "gemini": GeminiProvider,
}


def load_providers_from_config(config_path=None) -> List[LLMProvider]:
    """Parse the YAML config, match each entry to its API key, and build providers.

    Keys are read from the environment; `.env` next to this package is loaded
    first so `GROQ_API_KEY_1` etc. resolve without the caller wiring dotenv.
    """
    load_dotenv(_PACKAGE_DIR / ".env")

    config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    providers: List[LLMProvider] = []
    for conf in config.get("providers", []):
        name = conf["name"]
        api_key = os.getenv(conf["api_key_env"])
        if not api_key:
            logger.warning(f"Skipping {name}: env var '{conf['api_key_env']}' not set.")
            continue

        provider_cls = _PROVIDER_TYPES.get(conf.get("type"))
        if provider_cls is None:
            logger.warning(f"Skipping {name}: unknown provider type '{conf.get('type')}'.")
            continue

        providers.append(provider_cls(
            name=name,
            url=conf.get("url", ""),
            model=conf["model"],
            api_key=api_key,
            priority=conf["priority"],
            temperature=conf.get("temperature", 0.2),
        ))

    return providers
