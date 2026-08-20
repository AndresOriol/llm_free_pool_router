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

from llm_router.base_provider import (LLMProvider, is_decommissioned,
                                      is_transient)


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

    # Network errors carry no status; classify by exception class name.
    assert is_transient(TimeoutError("timed out"))[0] is True
    assert is_transient(ConnectionError("reset"))[0] is True

    # Unknown error with no status/name -> surface it, don't exhaust the pool.
    assert is_transient(ValueError("bug")) == (False, None)

    # -- a model retired upstream --------------------------------------------
    # The real Groq body, verbatim. It is a 404, so is_transient correctly calls
    # it fatal -- which is why it killed three runs before this was classified
    # separately.
    gone = _Exc("Error code: 404 - {'error': {'message': 'The model "
                "`llama-3.3-70b-versatile` does not exist or you do not have access "
                "to it.', 'code': 'model_not_found'}}", status_code=404)
    assert is_decommissioned(gone) is True
    assert is_transient(gone) == (False, None)

    # Gemini words it differently for the same thing.
    assert is_decommissioned(_Exc("404 NOT_FOUND. models/gemini-9-flash is not "
                                  "found for API version v1beta")) is True

    # Nothing else is a retirement -- a rate limit must still be a cooldown, and
    # a real bug must still surface.
    assert is_decommissioned(_Exc("rate_limit_exceeded", status_code=413)) is False
    assert is_decommissioned(_Exc("bad request", status_code=400)) is False
    assert is_decommissioned(ValueError("bug")) is False

    # A retired member leaves the pool and nothing brings it back -- not the
    # clock, which is the whole difference from a cooldown.
    class _P(LLMProvider):
        def build_chat_model(self):
            raise NotImplementedError

    provider = _P("Llama3_70b_groq_1", "", "llama-3.3-70b-versatile", "k", priority=1)
    assert provider.check_availability() is True
    provider.retire("code=model_not_found")
    assert provider.decommissioned is True
    assert provider.check_availability() is False
    provider.cooldown_until = 0.0  # as if every cooldown had long expired
    assert provider.check_availability() is False

    print("is_transient: all checks passed")


if __name__ == "__main__":
    _run()
