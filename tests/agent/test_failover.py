"""Checks for the failover loop: what happens to a run when a provider fails.

The loop is the project's whole reason to exist -- an unattended run must not
stop because one free-tier member is busy, or gone. Every case here is one way
a member can fail and what the pool is supposed to do about it.

    python -m pytest tests/agent/test_failover.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.runtime.chat_model import RouterChatModel
from langchain_core.messages import AIMessage
from llm_router.base_provider import LLMProvider
from llm_router.router import AutonomousLLMRouter
from tests.agent.test_harness import check


class _Exc(Exception):
    """A provider SDK exception, with the status the SDK would carry."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code


class _Chat:
    """Stands in for a provider's LangChain model. Raises, or answers."""

    def __init__(self, raises=None, answer="ok"):
        self.raises = raises
        self.answer = answer
        self.calls = 0

    def invoke(self, messages, **kwargs):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return AIMessage(content=self.answer)


class _Provider(LLMProvider):
    """A real provider -- real availability, cooldown and retirement state --
    with the network call replaced."""

    def __init__(self, name, priority, chat, model="m"):
        super().__init__(name=name, url="", model=model, api_key="k",
                         priority=priority, max_input_tokens=100_000)
        self._stub = chat

    def build_chat_model(self):
        return self._stub


GONE = _Exc("Error code: 404 - {'error': {'message': 'The model `llama-3.3-70b-"
            "versatile` does not exist or you do not have access to it.', "
            "'code': 'model_not_found'}}", status_code=404)


def _model(*providers):
    return RouterChatModel(router=AutonomousLLMRouter(list(providers)),
                           max_retries=len(providers) + 3)


def test_a_retired_model_does_not_kill_the_run():
    """The failure this was written for.

    A 404 is a 4xx, so `is_transient` calls it fatal and it propagated out of
    the loop -- killing three runs before it was classified separately. The
    pool holds nineteen other members; one of them should have served it.
    """
    dead = _Provider("dead_groq", priority=1, chat=_Chat(raises=GONE),
                     model="llama-3.3-70b-versatile")
    alive = _Provider("live_gemini", priority=2, chat=_Chat(answer="served"))

    reply = _model(dead, alive).invoke("hello")

    check("the run survived and got an answer", reply.content == "served")
    check("the dead member was retired", dead.decommissioned is True)
    check("the live one served it", alive._stub.calls == 1)


def test_a_retired_model_is_never_offered_again():
    """Retirement is not a cooldown. Nothing brings it back, so a later call
    must not spend an attempt rediscovering that the model is gone."""
    dead = _Provider("dead_groq", priority=1, chat=_Chat(raises=GONE))
    alive = _Provider("live_gemini", priority=2, chat=_Chat(answer="served"))
    model = _model(dead, alive)

    model.invoke("first")
    model.invoke("second")

    check(f"the dead member was tried once, not twice: {dead._stub.calls}",
          dead._stub.calls == 1)
    check("and the live one took both", alive._stub.calls == 2)


def test_a_pool_of_nothing_but_retired_models_says_so():
    """It must not sit waiting for a member that is never coming back: a
    retired provider has no cooldown to expire, so there is nothing to wait
    for and the caller needs to hear that rather than sleep."""
    dead = _Provider("dead_a", priority=1, chat=_Chat(raises=GONE))
    also_dead = _Provider("dead_b", priority=2, chat=_Chat(raises=GONE))
    router = AutonomousLLMRouter([dead, also_dead])

    raised = None
    try:
        RouterChatModel(router=router, max_retries=5).invoke("hello")
    except Exception as exc:  # noqa: BLE001 - the point is which one
        raised = exc

    check(f"it gave up rather than hanging, got {raised!r}",
          isinstance(raised, RuntimeError))
    check("nothing is waiting on a retired member",
          router.seconds_until_available() is None)


def test_a_rate_limited_member_still_gets_a_cooldown():
    """The retirement path must not swallow the ordinary case: a busy member is
    coming back, and benching it temporarily is the whole router."""
    busy = _Provider("busy_groq", priority=1,
                     chat=_Chat(raises=_Exc("rate_limit_exceeded", status_code=413)))
    alive = _Provider("live_gemini", priority=2, chat=_Chat(answer="served"))

    reply = _model(busy, alive).invoke("hello")

    check("the run survived", reply.content == "served")
    check("the busy member is cooling down, not retired",
          busy.decommissioned is False and busy.is_available is False)
    check("and it has a time to come back at", busy.cooldown_until > 0)


def test_a_real_bug_still_surfaces():
    """A pool that swallows everything hides the caller's own mistakes."""
    broken = _Provider("groq", priority=1, chat=_Chat(raises=ValueError("bug")))

    raised = None
    try:
        _model(broken).invoke("hello")
    except Exception as exc:  # noqa: BLE001 - the point is which one
        raised = exc

    check(f"the error was raised, not rerouted past, got {raised!r}",
          isinstance(raised, ValueError))


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("failover: all checks passed")
