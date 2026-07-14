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


def estimate_tokens(messages, tools=None) -> int:
    """Rough token estimate for a request: ~4 chars per token over the
    serialized messages and tool schemas.

    Deliberately a cheap heuristic -- no per-provider tokenizer, no dependency.
    It only needs to be good enough to keep a request off a model whose window
    it clearly overflows, so the router can pick a higher-capacity provider up
    front instead of walking the whole small-TPM pool on 413s.
    """
    chars = 0
    for message in messages or []:
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        chars += len(str(content))
    for tool in tools or []:
        chars += len(str(tool))
    return chars // 4


def provider_error_detail(exc: Exception) -> Optional[str]:
    """Pull the provider's structured error body out of an SDK exception.

    Groq's `tool_use_failed` (HTTP 400) carries the model's raw malformed output
    in `error.failed_generation` -- the key clue for *why* a tool call was
    rejected. The router reroutes past this error, so without surfacing the body
    the failure is invisible. Returns a readable one-liner, or None when there's
    no structured body to show.
    """
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        response = getattr(exc, "response", None)
        try:
            body = response.json() if response is not None else None
        except Exception:  # noqa: BLE001 - body simply isn't JSON
            body = None
    if not isinstance(body, dict):
        return None

    error = body.get("error", body)
    if not isinstance(error, dict):
        return None

    parts = [f"{key}={error[key]}"
             for key in ("code", "message", "failed_generation")
             if error.get(key)]
    return "; ".join(parts) or None


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
    if ("rate_limit" in message or "rate limit" in message or "too many requests" in message
            # Gemini signals quota/rate exhaustion as RESOURCE_EXHAUSTED and its
            # LangChain wrapper (ChatGoogleGenerativeAIError) exposes no numeric
            # status, so match the wording -- a free-tier quota hit is transient.
            or "resource_exhausted" in message or "exceeded your current quota" in message):
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
                 priority: int, temperature: float = 0.2,
                 max_input_tokens: Optional[int] = None):
        self.name = name
        self.url = url
        self.model = model
        self.api_key = api_key
        self.priority = priority
        self.temperature = temperature
        # Per-request token ceiling (min of the model's TPM and context window).
        # None means "unknown, never filter it out" -- see get_best_provider.
        self.max_input_tokens = max_input_tokens

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
