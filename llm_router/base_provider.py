import time
import logging
import asyncio
from abc import ABC, abstractmethod
from typing import Optional, Tuple, List, Dict
import aiohttp

logger = logging.getLogger("LLMRouter")

CONECTION_TIMEOUT=120 # We keep conservative for reasoning tasks, where models take a lot of time (2 mins)

class LLMProvider(ABC):
    """Abstract base class for an asynchronous LLM provider within the pool."""

    def __init__(self, name: str, url: str, model: str, api_key: str, priority: int):
        self.name = name
        self.url = url
        self.model = model
        self.api_key = api_key
        self.priority = priority

        self.is_available = True
        self.cooldown_until = 0.0
        self.consecutive_failures = 0

    @abstractmethod
    def _build_request(self, messages: List[Dict[str, str]]) -> Tuple[str, dict, dict]:
        """Build the provider-specific HTTP request using a standard message array."""
        pass

    @abstractmethod
    def _parse_response(self, res_json: dict) -> str:
        """Extract the response text from the provider's JSON payload."""
        pass

    def check_availability(self) -> bool:
        """Check whether the provider has served its penalty time."""
        
        if not self.is_available and time.time() > self.cooldown_until:
            self.is_available = True
            self.consecutive_failures = 0
            logger.info(f"{self.name} has finished its cooldown and is available again.")
        
        return self.is_available

    def trigger_cooldown(self, retry_after: Optional[int] = None):
        """Temporarily block the provider. Uses Retry-After header or exponential backoff."""
        self.is_available = False
        self.consecutive_failures += 1
        
        if retry_after:
            duration = retry_after
        else:
            # Exponential backoff capped at 300 seconds (5 mins)
            duration = min(30 * (2 ** (self.consecutive_failures - 1)), 300)
            
        self.cooldown_until = time.time() + duration
        logger.warning(f"{self.name} exhausted/failed. Entering cooldown for {duration}s.")

    async def send_request(self, session: aiohttp.ClientSession, messages: List[Dict[str, str]]) -> Optional[str]:
        """Asynchronous entry point for the router."""
        url, headers, payload = self._build_request(messages)
        return await self._execute_post(session, url, headers, payload)

    async def _execute_post(self, session: aiohttp.ClientSession, url: str, headers: dict, payload: dict) -> Optional[str]:
        """Execute the HTTP call asynchronously and handle common errors."""
        try:
            async with session.post(url, json=payload, headers=headers, timeout=CONECTION_TIMEOUT) as response:
                if response.status == 429 or response.status >= 500:
                    retry_header = response.headers.get("Retry-After")
                    retry_secs = int(retry_header) if retry_header and retry_header.isdigit() else None
                    self.trigger_cooldown(retry_secs)
                    return None
                
                response.raise_for_status()
                data = await response.json()
                return self._parse_response(data)
                
        except asyncio.TimeoutError:
            logger.error(f"Timeout error with {self.name}. The model lasted longer than {CONECTION_TIMEOUT} seconds to respond.")
            self.trigger_cooldown()
            return None
        except aiohttp.ClientError as e:
            logger.error(f"Connection error with {self.name}: {e}")
            self.trigger_cooldown()
            return None
        except (KeyError, IndexError, TypeError) as e:
            logger.error(f"Error parsing response from {self.name}: {e}")
            return None