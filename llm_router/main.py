import os
import yaml
import asyncio
import logging
from dotenv import load_dotenv

from providers import OpenAICompatibleProvider, GeminiProvider
from router import AutonomousLLMRouter

# Configure standard logging

logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s',
    force=True
)
logger = logging.getLogger("Main")

load_dotenv()

def load_providers_from_config(config_path: str = "llm_router/config.yaml"):
    """Dynamically parses the YAML config and matches it against ENV variables."""
    providers = []
    
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
        
    for p_conf in config.get("providers", []):
        name = p_conf["name"]
        url = p_conf["url"]
        model = p_conf["model"]
        priority = p_conf["priority"]
        
        # Securely load the key using the variable name defined in YAML
        env_var = p_conf["api_key_env"]
        api_key = os.getenv(env_var)
        
        if not api_key:
            logger.warning(f"Skipping {name}: Environment variable '{env_var}' not found in .env.")
            continue

        provider_type = p_conf.get("type")
        if provider_type == "openai_compatible":
            providers.append(OpenAICompatibleProvider(name, url, model, api_key, priority))
        elif provider_type == "gemini":
            providers.append(GeminiProvider(name, url, model, api_key, priority))
        else:
            logger.warning(f"Unknown provider type '{provider_type}' for {name}.")
            
    return providers

async def main():
    pool = load_providers_from_config()
    
    if not pool:
        logger.error("No providers loaded. Verify your .env file and config.yaml.")
        return

    router = AutonomousLLMRouter(providers=pool)

    # Standardized schema allows for system instructions and conversation history
    messages = [
        {"role": "system", "content": "You are a concise scientific assistant."},
        {"role": "user", "content": "Explica brevemente qué es el entrelazamiento cuántico."}
    ]

    try:
        logger.info("Starting autonomous generation loop...")
        resultado = await router.generate(messages)
        print("\n --- FINAL ANSWER ---")
        print(resultado)
    except Exception as e:
        logger.error(f"Agent execution failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())