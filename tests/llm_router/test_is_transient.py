"""One runnable check for is_transient(), the core reroute-vs-raise classifier.

No framework: `python -m tests.llm_router.test_is_transient` (or run the
file). Each case is the smallest thing that fails if the classification
breaks.
"""

import sys
from pathlib import Path

# Allow running either as `python -m tests.llm_router.test_is_transient` or
# `python tests/llm_router/test_is_transient.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from llm_router.base_provider import is_transient


class _Exc(Exception):
    """Stand-in for a provider SDK exception with optional status/headers."""

    def __init__(self, message="", status_code=None, code=None, retry_after=None):
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code
        if retry_after is not None:
            self.response = type("R", (), {"headers": {"retry-after": str(retry_after)}})()


def _run():
    # Groq signals tokens-per-minute exhaustion as 413 + a rate_limit body, not
    # 429 -- must be transient despite the 4xx status.
    assert is_transient(_Exc("Error: rate_limit_exceeded", status_code=413)) == (True, None)

    # Plain rate-limit wording with no status is still transient.
    assert is_transient(_Exc("Too Many Requests"))[0] is True

    # Retry-After header is parsed and returned.
    assert is_transient(_Exc("rate limit", status_code=429, retry_after=42)) == (True, 42)

    # 5xx and the retryable 4xx set are transient.
    assert is_transient(_Exc("boom", status_code=503))[0] is True
    assert is_transient(_Exc("conflict", status_code=409))[0] is True

    # A real client error (bad request/auth/model) must NOT reroute.
    assert is_transient(_Exc("invalid model", status_code=400)) == (False, None)

    # A malformed tool call (Groq 400 `tool_use_failed`) IS transient despite the
    # 400 -- it's a model-output glitch, so reroute to another model.
    assert is_transient(_Exc("tool call validation failed: ... code tool_use_failed",
                             status_code=400)) == (True, None)

    # Gemini quota exhaustion arrives as RESOURCE_EXHAUSTED with no numeric status
    # on the wrapped exception -- must still be transient.
    assert is_transient(_Exc("Error calling model (RESOURCE_EXHAUSTED): 429 ..."))[0] is True

    # Gemini-style APIError exposes .code, not .status_code.
    assert is_transient(_Exc("overloaded", code=503))[0] is True

    # The wait is usually not in a header. langchain_google_genai re-raises the
    # quota refusal as a plain exception with no response object, so the only
    # copy left is the message -- which carries it twice, once truncated in a
    # RetryInfo field and once in prose. Both are read and the longer wins,
    # rounded up: retrying a fraction of a second early buys another refusal.
    gemini = _Exc("429 RESOURCE_EXHAUSTED. {'error': {'message': 'You exceeded your "
                  "current quota. Please retry in 37.677718404s.', 'details': "
                  "[{'@type': '...RetryInfo', 'retryDelay': '37s'}]}}")
    assert is_transient(gemini) == (True, 38), is_transient(gemini)

    # Groq says it in prose only.
    groq = _Exc("rate_limit_exceeded: Limit 8000, Used 6180, Requested 6247. "
                "Please try again in 33.2025s. Need more tokens?")
    assert is_transient(groq) == (True, 34), is_transient(groq)

    # Compound units, which is how a wait over a minute is quoted.
    assert is_transient(_Exc("rate limit. Please try again in 1m26.4s")) == (True, 87)

    # And nothing invented when no wait was offered. Model names and token
    # counts are full of digits followed by letters; none of them is a duration.
    assert is_transient(_Exc("rate limit reached, calm down")) == (True, None)
    assert is_transient(
        _Exc("rate limit on gemini-3.5-flash after 120b tokens")) == (True, None)

    # A header still wins when there is one, and an HTTP-date is not a number.
    assert is_transient(_Exc("rate limit", retry_after="4.5")) == (True, 5)
    assert is_transient(
        _Exc("rate limit", retry_after="Wed, 21 Oct 2026 07:28:00 GMT")) == (True, None)

    # Network errors carry no status; classify by exception class name.
    assert is_transient(TimeoutError("timed out"))[0] is True
    assert is_transient(ConnectionError("reset"))[0] is True

    # Unknown error with no status/name -> surface it, don't exhaust the pool.
    assert is_transient(ValueError("bug")) == (False, None)

    print("is_transient: all checks passed")


if __name__ == "__main__":
    _run()
