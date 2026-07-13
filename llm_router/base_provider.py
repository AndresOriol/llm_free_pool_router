import time
import logging
from abc import ABC, abstractmethod
from typing import Optional, Tuple

from langchain_core.language_models.chat_models import BaseChatModel

logger = logging.getLogger("LLMRouter")


def _retry_after(exc: Exception) -> Optional[int]:
    """Best-effort read of a Retry-After hint from a provider exception."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        value = headers.get("retry-after") or headers.get("Retry-After")
        if value and str(value).isdigit():
            return int(value)
    return None


def is_transient(exc: Exception) -> Tuple[bool, Optional[int]]:
    """Classify an exception raised while calling a provider.

    Returns (should_cooldown_and_retry, retry_after_seconds). Anything that means
    "this account/model is temporarily unusable, try another" is transient and
    triggers a reroute: rate limits, server errors (5xx), timeouts, connection
    errors. Clear client errors (bad request, auth) and anything without a status
    are fatal, so real bugs surface instead of silently exhausting the pool.

    Note: free tiers signal rate limits inconsistently -- Groq returns HTTP 413
    ("Request too large" for tokens-per-minute) with a `rate_limit_exceeded`
    body, not 429 -- so we match on the rate-limit signal first, before status.
    """
    message = str(getattr(exc, "message", "") or exc).lower()
    if "rate_limit" in message or "rate limit" in message or "too many requests" in message:
        return True, _retry_after(exc)

    # A malformed tool call is a per-model output glitch (small models sometimes
    # emit the args inside the tool name); Groq rejects it as HTTP 400
    # `tool_use_failed`. Reroute to another model rather than killing the run --
    # the next model usually formats it correctly. Matched before the status
    # check so it isn't swept up by the "other 4xx -> fatal" rule below.
    if "tool_use_failed" in message or "tool call validation failed" in message:
        return True, None

    # openai SDK exceptions expose .status_code; google-genai APIError exposes .code
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        code = getattr(exc, "code", None)
        status = code if isinstance(code, int) else None

    if isinstance(status, int):
        if status in (408, 409, 429) or status >= 500:
            return True, _retry_after(exc)
        return False, None  # other 4xx client error -> don't keep rerouting

    name = type(exc).__name__.lower()
    if "timeout" in name or "connection" in name:
        return True, None

    return False, None  # unknown error with no status -> surface it


class LLMProvider(ABC):
    """One free-tier account/model in the pool.

    Owns availability/cooldown state (the router's core value) and builds a
    LangChain chat model that does the actual API call, so tool calling and
    message conversion come from battle-tested provider packages.
    """

    def __init__(self, name: str, url: str, model: str, api_key: str,
                 priority: int, temperature: float = 0.2):
        self.name = name
        self.url = url
        self.model = model
        self.api_key = api_key
        self.priority = priority
        self.temperature = temperature

        self.is_available = True
        self.cooldown_until = 0.0
        self.consecutive_failures = 0
        self._chat: Optional[BaseChatModel] = None

    @abstractmethod
    def build_chat_model(self) -> BaseChatModel:
        """Build the LangChain chat model for this account/model."""
        raise NotImplementedError

    @property
    def chat(self) -> BaseChatModel:
        """The provider's LangChain chat model, built once and cached."""
        if self._chat is None:
            self._chat = self.build_chat_model()
        return self._chat

    def check_availability(self) -> bool:
        """Check whether the provider has served its penalty time."""
        if not self.is_available and time.time() > self.cooldown_until:
            self.is_available = True
            self.consecutive_failures = 0
            logger.info(f"{self.name} has finished its cooldown and is available again.")

        return self.is_available

    def trigger_cooldown(self, retry_after: Optional[int] = None):
        """Temporarily block the provider. Uses Retry-After or exponential backoff."""
        self.is_available = False
        self.consecutive_failures += 1

        if retry_after:
            duration = retry_after
        else:
            # Exponential backoff capped at 300 seconds (5 mins)
            duration = min(30 * (2 ** (self.consecutive_failures - 1)), 300)

        self.cooldown_until = time.time() + duration
        logger.warning(f"{self.name} exhausted/failed. Entering cooldown for {duration}s.")
