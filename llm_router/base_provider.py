import logging
import math
import re
import time
from abc import ABC, abstractmethod
from typing import Optional, Tuple

from langchain_core.language_models.chat_models import BaseChatModel

logger = logging.getLogger("LLMRouter")


_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
_DURATION = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|s|m|h)(?![a-z])", re.I)
# Google returns a RetryInfo block: "'retryDelay': '37s'".
_RETRY_FIELD = re.compile(r"""retry[_ ]?delay["']?\s*[:=]\s*["']?([\d.]+\s*[a-z]+)""", re.I)
# Both vendors also say it in prose: "Please try again in 33.2025s" (Groq),
# "Please retry in 37.677718404s" (Google).
_RETRY_PROSE = re.compile(
    r"(?:try again|retry)(?:\s+in)?\s+((?:\d+(?:\.\d+)?\s*(?:ms|s|m|h)\s*)+)", re.I)


def _duration_seconds(text: str) -> Optional[float]:
    """"1m26.4s" -> 86.4. Vendors quote waits in compound units, not seconds."""
    parts = _DURATION.findall(text or "")
    if not parts:
        return None
    return sum(float(value) * _UNITS[unit.lower()] for value, unit in parts)


def _retry_after(exc: Exception) -> Optional[int]:
    """How long the provider asked us to wait, from wherever it said so.

    The header is the polite place to put it and the one place our providers
    reliably don't. Google's quota refusal carries the wait twice -- once as a
    `retryDelay` field, once in prose -- and `langchain_google_genai` re-raises
    the whole thing as a plain exception with no response object, so headers are
    unreachable and the only copy left is the message text. That is not an edge
    case: it is every Gemini rate limit, which is most of the refusals this pool
    sees.

    Every hint found is collected and the longest wins, then rounded up. The
    structured field truncates (`37s` for a 37.68s wait) and retrying a fraction
    of a second early buys another refusal, so erring long costs nothing and
    erring short costs a request.
    """
    hints = []

    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        value = headers.get("retry-after") or headers.get("Retry-After")
        try:
            hints.append(float(value))
        except (TypeError, ValueError):
            pass  # an HTTP-date, or nothing at all

    message = str(getattr(exc, "message", "") or exc)
    for pattern in (_RETRY_FIELD, _RETRY_PROSE):
        found = pattern.search(message)
        seconds = _duration_seconds(found.group(1)) if found else None
        if seconds is not None:
            hints.append(seconds)

    return math.ceil(max(hints)) if hints else None


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


_RATE_LIMIT_SIGNALS = (
    "rate_limit",
    "rate limit",
    "too many requests",
    # Gemini signals quota/rate exhaustion as RESOURCE_EXHAUSTED and its
    # LangChain wrapper (ChatGoogleGenerativeAIError) exposes no numeric
    # status, so match the wording -- a free-tier quota hit is transient.
    "resource_exhausted",
    "exceeded your current quota",
)


def is_rate_limited(exc: Exception) -> bool:
    """Did the provider refuse this call for want of quota?

    Its own predicate because two callers need the same judgement for different
    reasons: `is_transient` reroutes on it, and the usage ledger records it as a
    distinct outcome so the panel can show an account being turned away rather
    than merely failing (see usage.py).

    Free tiers signal it inconsistently -- Groq returns HTTP 413 ("Request too
    large", for tokens-per-minute) with a `rate_limit_exceeded` body rather than
    a 429 -- so this matches on the wording, and is asked before any status.
    """
    message = str(getattr(exc, "message", "") or exc).lower()
    return any(signal in message for signal in _RATE_LIMIT_SIGNALS)


def _status_of(exc: Exception) -> Optional[int]:
    """HTTP status, wherever the SDK put it.

    openai exceptions expose `.status_code`; google-genai's APIError exposes
    `.code`.
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) else None


def is_decommissioned(exc: Exception) -> bool:
    """Is this pool member gone upstream, rather than busy?

    Free platforms retire models without notice and the SDK reports it as an
    ordinary 404. `is_transient` correctly refuses to retry a 4xx, so the error
    propagates and kills the whole run -- which has now happened three times,
    for `llama-4-scout`, `qwen3-32b` and `llama-3.3-70b-versatile`. The
    workaround each time was to delete the model from the eval pool by hand,
    which fixes the measurement and leaves an unattended run dying on the next
    retirement.

    A retired model is neither transient nor a bug in the caller: it is a member
    that will never work again. So it is dropped from the pool for the rest of
    the process and the run carries on with the others -- which is the whole
    point of holding a pool.

    **The status is not always reachable.** `langchain_google_genai` catches the
    SDK's `APIError` and re-raises its own `ChatGoogleGenerativeAIError`, which
    is a plain exception: no `.status_code`, no `.code`, just the original text
    in the message. So `_status_of` returns None and the 404 test below never
    fires. That is how `gemini-2.5-flash` -- retired with "no longer available
    to new users" -- killed both runs of a batch after this function was
    supposedly written to prevent exactly that. The status is therefore read out
    of the message too, which is the only place a wrapped error still has it.
    """
    return looks_decommissioned(str(getattr(exc, "message", "") or exc),
                                _status_of(exc))


def looks_decommissioned(message: str, status: Optional[int] = None) -> bool:
    """The same question asked of text rather than of an exception.

    Split out so the eval harness can classify a recorded `llm_error` detail
    without reconstructing the exception, and so the signals live in one place:
    a batch spent 135 of its 247 bounces on a model this list already
    recognised, and the second definition would have been the one that drifted.
    """
    message = message.lower()
    for signal in ("model_not_found",
                   "does not exist or you do not have access",
                   "is not found for api version",
                   # Google's wording when a model is closed to new users. It
                   # is a retirement, whatever the status says.
                   "no longer available"):
        if signal in message:
            return True
    if status == 404:
        return True
    # A wrapped 404, read out of the text. Both halves are required: a bare
    # `404` appears in plenty of messages that are not retirements (a token
    # count, a port, an id), and "not found" alone is said about files and
    # fields as often as about models.
    return bool(re.search(r"\b404\b", message)
                and ("not_found" in message or "not found" in message))


def is_unauthorized(exc: Exception) -> bool:
    """Is this member's *key* dead, rather than the model or the quota?

    A free plan can be withdrawn between one call and the next. Mid-session a
    Gemini account came back `401 UNAUTHENTICATED -- The bound service account
    is deleted or disabled`, `is_transient` read it as "a clear client error,
    so surface the bug", and the exception killed a run that still had six
    working accounts underneath it. That is the exact stall this pool exists to
    absorb: one member is gone, the others are fine.

    Auth was classed as fatal on the reasoning that a bad key is a
    misconfiguration the operator has to see. It still is -- so this is loud,
    and the run carries on. Only the account whose key it is loses anything.
    """
    return looks_unauthorized(str(getattr(exc, "message", "") or exc),
                              _status_of(exc))


def looks_unauthorized(message: str, status: Optional[int] = None) -> bool:
    """The same question asked of text, for the same reason as
    `looks_decommissioned`: the eval harness classifies recorded details, and
    one definition cannot drift from another that does not exist.
    """
    message = message.lower()
    for signal in ("unauthenticated",
                   "api key not valid",
                   "api_key_invalid",
                   "invalid api key",
                   "invalid_api_key",
                   "service account is deleted or disabled",
                   "permission_denied",
                   "account is not active",
                   "account deactivated"):
        if signal in message:
            return True
    if status in (401, 403):
        return True
    # A wrapped status, read out of the text -- `langchain_google_genai`
    # re-raises its own plain exception, so `_status_of` finds nothing. Both
    # halves are required: a bare 401 or 403 shows up in ids and token counts.
    return bool(re.search(r"\b(401|403)\b", message)
                and ("unauthenticated" in message or "unauthorized" in message
                     or "permission denied" in message
                     or "forbidden" in message))


def reached_provider(exc: Exception) -> bool:
    """Did this failed attempt actually get an answer from the provider?

    The usage ledger needs to know, because only an attempt the vendor answered
    spent anything against the account. A 429 or a 413 did: the request was
    sent, the vendor read it and said no. A connection error or a timeout may
    never have left this machine, and counting those as requests would inflate
    the panel with traffic the vendor never saw.

    Positive evidence only. Anything without a status and without the wording of
    a quota refusal is treated as never having arrived -- overstating remaining
    budget is the wrong way to be wrong, but so is inventing requests, and this
    branch is reached by bugs and local failures far more often than by silent
    successes.
    """
    return is_rate_limited(exc) or _status_of(exc) is not None


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
    if is_rate_limited(exc):
        return True, _retry_after(exc)

    message = str(getattr(exc, "message", "") or exc).lower()

    # A malformed tool call is a per-model output glitch (small models sometimes
    # emit the args inside the tool name); Groq rejects it as HTTP 400
    # `tool_use_failed`. Reroute to another model rather than killing the run --
    # the next model usually formats it correctly. Matched before the status
    # check so it isn't swept up by the "other 4xx -> fatal" rule below.
    if "tool_use_failed" in message or "tool call validation failed" in message:
        return True, None

    status = _status_of(exc)
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
                 max_input_tokens: Optional[int] = None,
                 platform: str = "", account: str = ""):
        self.name = name
        self.url = url
        self.model = model
        # Which signup and which vendor this member draws on. The composed
        # `name` already encodes both, but only as a string to be re-split;
        # the usage ledger needs them apart, because a free tier's real budget
        # is per account (Groq shares one across every model on it) and per
        # platform, not per pool member.
        self.platform = platform
        self.account = account
        self.api_key = api_key
        self.priority = priority
        self.temperature = temperature
        # Per-request token ceiling (min of the model's TPM and context window).
        # None means "unknown, never filter it out" -- see get_best_provider.
        self.max_input_tokens = max_input_tokens

        self.is_available = True
        self.cooldown_until = 0.0
        self.consecutive_failures = 0
        # Set once the platform says this model no longer exists. Distinct from
        # a cooldown, which is a wait: this one never ends.
        self.decommissioned = False
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
        if self.decommissioned:
            return False
        if not self.is_available and time.time() > self.cooldown_until:
            self.is_available = True
            logger.info(f"{self.name} has finished its cooldown and is available again.")

        return self.is_available

    def retire(self, reason: str = "", remedy: str = "") -> None:
        """Drop this member from the pool for the rest of the process.

        Not a cooldown. Nothing brings it back, so this logs at ERROR with the
        thing a human has to go and do. `remedy` says what that is, because
        there are now two ways to end up here and they want opposite actions: a
        model retired upstream should be deleted from the config, while an
        account whose key died should have its key replaced and its models left
        alone. Telling the operator to delete the config entry for a dead key
        would cost them nine working models when the key is renewed.
        """
        self.decommissioned = True
        self.is_available = False
        logger.error(f"{self.name} dropped from the pool for this process "
                     f"(model={self.model}). "
                     f"{remedy or 'It is gone upstream; remove it from the config.'} "
                     f"{reason}".rstrip())

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
