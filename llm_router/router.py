import logging
import asyncio
import aiohttp
from typing import List, Optional, Dict
from base_provider import LLMProvider

logger = logging.getLogger("LLMRouter")

class AutonomousLLMRouter:
    """Routes async requests to the best available provider."""

    def __init__(self, providers: List[LLMProvider]):
        self.providers = providers

    def get_best_provider(self) -> Optional[LLMProvider]:
        """Filters available providers and sorts by strict priority."""
        available = [p for p in self.providers if p.check_availability()]
        
        if not available:
            return None
            
        available.sort(key=lambda p: p.priority)
        return available[0]

    async def generate(self, messages: List[Dict[str, str]], max_retries: int = 5) -> str:
        """Attempts to route the standard message payload asynchronously."""
        retries = 0
        
        async with aiohttp.ClientSession() as session:
            while retries < max_retries:
                provider = self.get_best_provider()

                if not provider:
                    logger.error("Critical Error: All providers are in cooldown.")
                    raise RuntimeError("All providers exhausted. Circuit broken.")

                logger.info(f"Routing to: {provider.name} (Model: {provider.model}) [Priority: {provider.priority}]")
                answer = await provider.send_request(session, messages)

                if answer:
                    return answer

                logger.info("Provider failed. Rerouting to next available provider...")
                retries += 1
                await asyncio.sleep(1) # Tiny buffer before the next loop
                
        raise RuntimeError("Max retries exceeded across the provider pool.")