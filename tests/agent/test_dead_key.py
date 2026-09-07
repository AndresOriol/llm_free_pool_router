"""A dead API key must bench its account, not end the run.

Mid-session a Gemini account returned `401 UNAUTHENTICATED -- The bound service
account is deleted or disabled`. `is_transient` read it as a clear client error
and refused to reroute, so the exception propagated and killed a run that still
had six working accounts under it. That is the exact stall the pool exists to
absorb, so it is now a retirement of the account rather than a fatal error.
"""

import pytest

from llm_router.base_provider import (is_transient, looks_decommissioned,
                                      looks_unauthorized)

DEAD_KEY = ("Error calling model 'gemini-3.8-flash' (UNAUTHENTICATED): "
            "401 UNAUTHENTICATED. {'error': {'code': 401, 'message': 'The bound "
            "service account is deleted or disabled. The service account bound "
            "to the API key must be active.', 'status': 'UNAUTHENTICATED'}}")


@pytest.mark.parametrize("message", [
    DEAD_KEY,
    "API key not valid. Please pass a valid API key.",
    "401 Unauthorized",
    "403 Forbidden",
    "Error code: 401 - {'error': {'code': 'invalid_api_key'}}",
    "PERMISSION_DENIED: the caller does not have permission",
])
def test_a_dead_key_is_recognised(message):
    assert looks_unauthorized(message) is True


@pytest.mark.parametrize("message", [
    "429 RESOURCE_EXHAUSTED. Quota exceeded for metric: generate_content",
    "404 model_not_found: gemini-2.5-flash is not found for API version v1beta",
    "500 internal error",
    # The digits alone must not convict: they show up in counts and ids.
    "Routing to Gemini_3_8_Flash (model=gemini-3.8-flash, ~401 tok).",
    "read 403 lines from the file",
])
def test_an_ordinary_failure_is_not_mistaken_for_a_dead_key(message):
    assert looks_unauthorized(message) is False


def test_status_alone_is_enough_when_the_sdk_exposes_it():
    assert looks_unauthorized("something went wrong", 401) is True
    assert looks_unauthorized("something went wrong", 403) is True
    assert looks_unauthorized("something went wrong", 429) is False


def test_a_dead_key_is_not_a_retirement():
    """Different remedies: one means replace the key, the other edit the config."""
    assert looks_decommissioned(DEAD_KEY) is False


def test_the_classifier_still_calls_it_non_transient():
    """`is_transient` is deliberately unchanged -- a cooldown is a wait, and no
    amount of waiting revives a deleted service account. The reroute comes from
    the retirement branch above it."""
    class _Exc(Exception):
        pass
    exc = _Exc(DEAD_KEY)
    assert is_transient(exc) == (False, None)


class _Member:
    """The parts of a pool member `_retire_account` touches."""

    def __init__(self, name, account):
        self.name, self.account = name, account
        self.decommissioned = False
        self.reason = self.remedy = None

    def retire(self, reason="", remedy=""):
        self.decommissioned = True
        self.reason, self.remedy = reason, remedy


class _Router:
    def __init__(self, providers):
        self.providers = providers


def _model_with(providers):
    from agent.runtime.chat_model import RouterChatModel
    return RouterChatModel(router=_Router(providers))


def test_a_dead_key_benches_every_member_on_that_account():
    """The key is per account, so one 401 condemns all nine of its models.

    Benching them one failure at a time would spend a wasted call on each.
    """
    dead = [_Member(f"M{i}_gemini_2", "gemini_2") for i in range(3)]
    alive = [_Member("M0_gemini_3", "gemini_3")]
    _model_with(dead + alive)._retire_account(dead[0], "401 UNAUTHENTICATED")
    assert all(m.decommissioned for m in dead)
    assert not alive[0].decommissioned


def test_it_never_benches_more_than_the_account_it_was_given():
    lone = _Member("M0_groq_1", "groq_1")
    others = [_Member("M1_gemini_1", "gemini_1")]
    _model_with([lone] + others)._retire_account(lone, "bad key")
    assert lone.decommissioned and not others[0].decommissioned


def test_a_member_with_no_account_still_gets_retired():
    """Falls back to the one member rather than silently benching nothing."""
    orphan = _Member("M0", "")
    _model_with([orphan])._retire_account(orphan, "bad key")
    assert orphan.decommissioned


def test_the_remedy_says_replace_the_key_not_the_model():
    """`retire` otherwise tells the operator to delete the config entry, which
    for a dead key would cost them every model on the account."""
    dead = _Member("M0_gemini_2", "gemini_2")
    _model_with([dead])._retire_account(dead, "401")
    assert "replace the key" in dead.remedy.lower()
    assert "remove it from the config" not in dead.remedy.lower()
