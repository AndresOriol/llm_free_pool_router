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
    """Parse the YAML config and build one provider per (model, account) pair.

    Each model is listed once per platform; it's fanned out across every
    account on that platform, so a model backed by several accounts doesn't
    need to be repeated in the config. Keys are read from the environment;
    `.env` next to this package is loaded first so `GROQ_API_KEY_1` etc.
    resolve without the caller wiring dotenv.
    """
    load_dotenv(_PACKAGE_DIR / ".env")

    config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    accounts_by_platform: dict = {}
    for account in config.get("accounts", []):
        accounts_by_platform.setdefault(account["platform"], []).append(account)

    providers: List[LLMProvider] = []
    for conf in config.get("models", []):
        name = conf["name"]
        platform = conf["platform"]
        matching_accounts = accounts_by_platform.get(platform, [])
        if not matching_accounts:
            logger.warning(f"Skipping {name}: no accounts for platform '{platform}'.")
            continue

        for account in matching_accounts:
            provider_name = f"{name}_{account['name']}"

            api_key = os.getenv(account["api_key_env"])
            if not api_key:
                logger.warning(f"Skipping {provider_name}: env var '{account['api_key_env']}' not set.")
                continue

            provider_cls = _PROVIDER_TYPES.get(account.get("type"))
            if provider_cls is None:
                logger.warning(f"Skipping {provider_name}: unknown provider type '{account.get('type')}'.")
                continue

            providers.append(provider_cls(
                name=provider_name,
                url=account.get("url", ""),
                model=conf["model"],
                api_key=api_key,
                priority=conf["priority"],
                temperature=conf.get("temperature", 0.2),
            ))

    return providers
