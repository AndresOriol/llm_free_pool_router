"""LangChain chat model that fronts the free-tier pool.

This is the single object a deep-agents / LangGraph loop holds as its model.
Every invocation runs the router's failover: pick the highest-priority available
account, call its LangChain chat model, and on a transient error (rate limit,
5xx, timeout) put that account in cooldown and reroute to the next one. So a
usage limit hit mid-task is transparent -- the next step just runs on another
account/model.
"""

import asyncio
import logging
import time
from typing import Any, List, Optional, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict

from llm_router.base_provider import is_transient

logger = logging.getLogger("LLMRouter")

# Never sleep longer than this in one wait when the whole pool is cooling down.
_MAX_WAIT_SECONDS = 300.0


class RouterChatModel(BaseChatModel):
    """A BaseChatModel that routes each call across the pooled providers."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    router: Any
    max_retries: int = 6
    bound_tools: Optional[List[Any]] = None
    bind_kwargs: dict = {}

    @property
    def _llm_type(self) -> str:
        return "router"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "RouterChatModel":
        """Carry the tools/kwargs so each provider's model gets them per call."""
        return self.model_copy(update={
            "bound_tools": list(tools),
            "bind_kwargs": kwargs,
        })

    def _underlying(self, provider):
        """The selected provider's LangChain model, with tools bound if any."""
        model = provider.chat
        if self.bound_tools:
            model = model.bind_tools(self.bound_tools, **self.bind_kwargs)
        return model

    @staticmethod
    def _result(message: BaseMessage) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _generate(self, messages: List[BaseMessage], stop=None,
                  run_manager=None, **kwargs) -> ChatResult:
        last_exc: Optional[Exception] = None
        for _ in range(self.max_retries):
            provider = self.router.get_best_provider()
            if provider is None:
                if self._wait_for_cooldown(sleep=time.sleep):
                    continue
                break

            logger.info(f"Routing to {provider.name} (model={provider.model}).")
            try:
                message = self._underlying(provider).invoke(messages, stop=stop, **kwargs)
                return self._result(message)
            except Exception as exc:  # noqa: BLE001 - classified below
                if not self._handle_failure(provider, exc):
                    raise
                last_exc = exc

        raise RuntimeError("All providers exhausted across the pool.") from last_exc

    async def _agenerate(self, messages: List[BaseMessage], stop=None,
                         run_manager=None, **kwargs) -> ChatResult:
        last_exc: Optional[Exception] = None
        for _ in range(self.max_retries):
            provider = self.router.get_best_provider()
            if provider is None:
                if await self._wait_for_cooldown(sleep=asyncio.sleep, is_async=True):
                    continue
                break

            logger.info(f"Routing to {provider.name} (model={provider.model}).")
            try:
                message = await self._underlying(provider).ainvoke(messages, stop=stop, **kwargs)
                return self._result(message)
            except Exception as exc:  # noqa: BLE001 - classified below
                if not self._handle_failure(provider, exc):
                    raise
                last_exc = exc

        raise RuntimeError("All providers exhausted across the pool.") from last_exc

    def _handle_failure(self, provider, exc: Exception) -> bool:
        """Cooldown + reroute on transient errors; return False to re-raise."""
        transient, retry_after = is_transient(exc)
        if not transient:
            logger.error(f"{provider.name} failed with a non-transient error: {exc!r}")
            return False
        logger.warning(f"{provider.name} transient failure ({exc!r}); rerouting.")
        provider.trigger_cooldown(retry_after)
        return True

    def _wait_for_cooldown(self, sleep, is_async: bool = False):
        """Sleep until the soonest account leaves cooldown. Returns a coroutine
        when is_async, else a bool. False means nothing is coming back."""
        wait = self.router.seconds_until_available()
        if wait is None:
            return _noop_false() if is_async else False
        delay = min(wait, _MAX_WAIT_SECONDS)
        logger.info(f"Whole pool in cooldown; waiting {delay:.0f}s for the next account.")
        if is_async:
            return _sleep_then_true(sleep, delay)
        sleep(delay)
        return True


async def _noop_false() -> bool:
    return False


async def _sleep_then_true(sleep, delay) -> bool:
    await sleep(delay)
    return True
