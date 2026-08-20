"""LangChain chat model that fronts the free-tier pool.

This is the single object a deep-agents / LangGraph loop holds as its model.
Every invocation runs the router's failover: pick the highest-priority available
account, call its LangChain chat model, and on a transient error (rate limit,
5xx, timeout) put that account in cooldown and reroute to the next one. So a
usage limit hit mid-task is transparent -- the next step just runs on another
account/model.
"""

import logging
import time
from typing import Any, List, Optional, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict

from llm_router.base_provider import (estimate_tokens, is_decommissioned,
                                      is_transient, provider_error_detail)

logger = logging.getLogger("LLMRouter")

# Never sleep longer than this in one wait when the whole pool is cooling down.
_MAX_WAIT_SECONDS = 300.0


class RouterChatModel(BaseChatModel):
    """A BaseChatModel that routes each call across the pooled providers.

    Only `_generate` (sync) is implemented; the current callers are all sync
    (the smoke test's `.invoke()`, the agent's `agent.stream()`). Async callers
    get BaseChatModel's default `_agenerate`, which runs `_generate` in a
    thread.
    # ponytail: async bridged via thread — fine for one agent at a time; add a
    # real `_agenerate` only if high async concurrency ever matters.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    router: Any
    max_retries: int = 6
    bound_tools: Optional[List[Any]] = None
    bind_kwargs: dict = {}
    # Config handed to the provider's own call. Leave None under LangGraph: the
    # provider call inherits the ambient run context and nests correctly on its
    # own (see _generate). A caller driving this model directly -- no graph, so
    # no ambient context -- gets no provider-level trace at all unless it passes
    # its config here, which would silently cost the eval metrics that count
    # provider calls and failover bounces.
    provider_config: Optional[dict] = None
    # Minimum context window this caller wants, in tokens. A role whose job is
    # judgement rather than mechanics declares one so the pool routes it to a
    # wide-context member instead of whichever narrow account happens to be
    # warm. None (the default) leaves routing exactly as it was.
    min_context: Optional[int] = None

    def for_context(self, min_context: Optional[int]) -> "RouterChatModel":
        """A copy of this model that demands a context floor."""
        return self.model_copy(update={"min_context": min_context})

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
        # Size the request once so routing can skip models it would overflow.
        estimated = estimate_tokens(messages, self.bound_tools)
        for _ in range(self.max_retries):
            provider = self.router.get_best_provider(estimated, self.min_context)
            if provider is None:
                if self._wait_for_cooldown():
                    continue
                break

            logger.info(f"Routing to {provider.name} (model={provider.model}, "
                        f"~{estimated} tok).")
            try:
                # No explicit config: the provider call inherits the ambient run
                # context, so each attempt is traced under the current agent step
                # (showing which model served it, and any failed attempts before
                # it). Passing a hand-built child manager here doesn't change that
                # nesting and trips the tracer's run_map ("No indexed run ID").
                message = self._underlying(provider).invoke(
                    messages, config=self.provider_config, stop=stop, **kwargs)
                return self._result(message)
            except Exception as exc:  # noqa: BLE001 - classified below
                if not self._handle_failure(provider, exc, run_manager):
                    raise
                last_exc = exc

        raise RuntimeError("All providers exhausted across the pool.") from last_exc

    def _handle_failure(self, provider, exc: Exception, run_manager=None) -> bool:
        """Cooldown + reroute on transient errors; return False to re-raise.

        A model retired upstream is dropped from the pool permanently rather
        than cooled down; everything else transient gets a cooldown.

        A rerouted error is easy to lose track of, so surface the provider's
        error body (Groq's `tool_use_failed` puts the model's raw malformed
        output in `failed_generation`): log it at WARNING and, when tracing,
        attach it to the run via `on_text` so the failed attempt shows up in
        LangSmith instead of vanishing behind the successful reroute.
        """
        detail = provider_error_detail(exc)

        # Checked before is_transient, which would call this a fatal 4xx and
        # kill the run. The model is gone, so the pool stops offering it and the
        # loop tries the next member instead -- no cooldown, because a cooldown
        # is a wait and there is nothing to wait for.
        if is_decommissioned(exc):
            provider.retire(detail or repr(exc))
            return True

        transient, retry_after = is_transient(exc)
        if not transient:
            logger.error(f"{provider.name} failed with a non-transient error: {exc!r}")
            return False

        reason = detail or repr(exc)
        logger.warning(f"{provider.name} transient failure; rerouting. {reason}")
        if detail and run_manager is not None:
            run_manager.on_text(f"\n[router] {provider.name} failed ({provider.model}): "
                                f"{detail}\n")
        provider.trigger_cooldown(retry_after)
        return True

    def _wait_for_cooldown(self) -> bool:
        """Sleep until the soonest account leaves cooldown. Returns False when
        nothing is coming back (no account in cooldown to wait for)."""
        wait = self.router.seconds_until_available()
        if wait is None:
            return False
        delay = min(wait, _MAX_WAIT_SECONDS)
        logger.info(f"Whole pool in cooldown; waiting {delay:.0f}s for the next account.")
        time.sleep(delay)
        return True
