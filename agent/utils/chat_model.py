"""LangChain chat model that fronts the free-tier pool.

This is the single object a deep-agents / LangGraph loop holds as its model.
Every invocation runs the router's failover: pick the highest-priority available
account, call its LangChain chat model, and on a transient error (rate limit,
5xx, timeout) put that account in cooldown and reroute to the next one. So a
usage limit hit mid-task is transparent -- the next step just runs on another
account/model.
"""

import logging
import math
import time
import uuid
from typing import Any, List, Optional, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict

from llm_router import usage
from llm_router.base_provider import (MODEL_UNAVAILABLE_COOLDOWN,
                                      estimate_tokens, is_decommissioned,
                                      failure_diagnostics,
                                      is_model_unavailable,
                                      is_rate_limited, is_transient,
                                      is_unauthorized,
                                      provider_error_detail, reached_provider)
from llm_router.quota.windows import resets_in

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
    # Whether that floor is a hard filter. False keeps the original behaviour --
    # prefer a wide member, settle for a narrow one rather than stall. True
    # refuses to route below the floor and waits instead, which is what a
    # conversational harness needs: its history cannot be trimmed to fit an
    # 8,000-token member without dropping what it is reasoning about.
    strict_context: bool = False
    # Which agent is holding this model, and the only reason the field exists.
    # deepagents keys a harness profile on `f"{provider}:{identifier}"` and
    # falls back to the provider alone, taking the identifier from `model_name`
    # or `model` on the instance (`deepagents/_models.py:get_model_identifier`).
    # This class declared neither, so every agent resolved to the bare
    # `routerchatmodel` key and one profile had to serve all three -- which is
    # why each of them reached past the profile for middleware instead. Setting
    # it gives an agent its own profile *layered on* the shared one; they merge
    # rather than replace, so `file_tools.py`'s `read_file` override survives.
    # None keeps the old single-profile behaviour
    # ([the deepagents skill](../../.claude/skills/deepagents/SKILL.md)).
    model_name: Optional[str] = None

    def for_agent(self, name: str) -> "RouterChatModel":
        """A copy of this model keyed to one agent's harness profile."""
        return self.model_copy(update={"model_name": name})

    def for_context(self, min_context: Optional[int],
                    strict: bool = False) -> "RouterChatModel":
        """A copy of this model that demands a context floor."""
        return self.model_copy(update={"min_context": min_context,
                                       "strict_context": strict})

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
        attempted: set[str] = set()
        request_id = uuid.uuid4().hex
        for attempt in range(1, self.max_retries + 1):
            provider = self.router.get_best_provider(estimated, self.min_context,
                                                     self.strict_context,
                                                     attempted=attempted)
            if provider is None:
                if self._wait_for_cooldown():
                    continue
                break

            logger.info(f"Routing to {provider.name} (model={provider.model}, "
                        f"~{estimated} tok).")
            attempted.add(provider.name)
            try:
                # The instant the request goes out, which is the one the vendor
                # meters it against. Recording when the reply landed instead put
                # a slow call in the wrong minute (llm_router/usage.py), and
                # every path out of this block reports it: the served call
                # below, and the refusal or error in _handle_failure.
                issued = time.time()
                began = time.monotonic()
                # No explicit config: the provider call inherits the ambient run
                # context, so each attempt is traced under the current agent step
                # (showing which model served it, and any failed attempts before
                # it). Passing a hand-built child manager here doesn't change that
                # nesting and trips the tracer's run_map ("No indexed run ID").
                message = self._underlying(provider).invoke(
                    messages, config=self.provider_config, stop=stop, **kwargs)
                provider.consecutive_failures = 0
                usage.record_call(provider, message, started=issued,
                                  request_id=request_id, attempt=attempt,
                                  estimated_tokens=estimated,
                                  duration_ms=(time.monotonic() - began) * 1000)
                return self._result(message)
            except Exception as exc:  # noqa: BLE001 - classified below
                if not self._handle_failure(provider, exc, run_manager, issued,
                                            request_id, attempt, estimated,
                                            (time.monotonic() - began) * 1000):
                    raise
                last_exc = exc

        raise RuntimeError("All providers exhausted across the pool.") from last_exc

    def _retire_account(self, provider, reason: str) -> None:
        """Drop every pool member whose key is the one that just failed."""
        account = getattr(provider, "account", "") or ""
        kin = [p for p in self.router.providers
               if account and getattr(p, "account", None) == account
               and not getattr(p, "decommissioned", False)]
        for member in kin or [provider]:
            member.retire(reason, remedy=f"Its account ({account or provider.name}) "
                                         f"rejected the key; replace the key rather "
                                         f"than the model.")
        logger.error("Account %r is unusable (%d member(s) dropped). Replace its "
                     "key; the run continues on the rest of the pool.",
                     account or provider.name, len(kin or [provider]))

    def _bench_model(self, provider) -> None:
        """Cool down every account's copy of a model the vendor says is full."""
        kin = [p for p in self.router.providers
               if p.model == provider.model
               and getattr(p, "platform", "") == getattr(provider, "platform", "")
               and not getattr(p, "decommissioned", False)]
        for member in kin or [provider]:
            member.trigger_cooldown(MODEL_UNAVAILABLE_COOLDOWN)
        logger.warning("%s is out of capacity; benched on %d account(s) for %ds.",
                       provider.model, len(kin or [provider]),
                       MODEL_UNAVAILABLE_COOLDOWN)

    def _handle_failure(self, provider, exc: Exception, run_manager=None,
                        started: Optional[float] = None,
                        request_id: Optional[str] = None,
                        attempt: Optional[int] = None,
                        estimated_tokens: Optional[int] = None,
                        duration_ms: Optional[float] = None) -> bool:
        """Cooldown + reroute on transient errors; return False to re-raise.

        A model retired upstream is dropped from the pool permanently rather
        than cooled down; everything else transient gets a cooldown.

        A rerouted error is easy to lose track of, so surface the provider's
        error body (Groq's `tool_use_failed` puts the model's raw malformed
        output in `failed_generation`): log it at WARNING and, when tracing,
        attach it to the run via `on_text` so the failed attempt shows up in
        LangSmith instead of vanishing behind the successful reroute.
        """
        transient, retry_after = is_transient(exc)
        detail = provider_error_detail(exc)

        # Recorded whatever the verdict: an attempt the provider *answered*
        # spent a request against the account's free-tier budget, so a run that
        # spent its afternoon being turned away should look expensive in the
        # panel rather than free. One that never got an answer is marked, and
        # the panel leaves it out of the count. A rate limit also carries the
        # provider's own Retry-After when it sent one -- the panel would
        # otherwise have to guess when the window clears (llm_router/usage.py).
        # Recorded before the retirement check below, so attempts burned on a
        # model that has gone away still show up as spend rather than as free.
        rate_limited = is_rate_limited(exc)
        diagnostics = failure_diagnostics(exc)
        usage.record(provider,
                     outcome="rate_limited" if rate_limited else "error",
                     retry_after=retry_after if rate_limited else None,
                     reached=reached_provider(exc),
                     started=started, request_id=request_id, attempt=attempt,
                     estimated_tokens=estimated_tokens,
                     duration_ms=duration_ms,
                     diagnostics=diagnostics)

        # Checked before the `not transient` branch below, which would call this
        # a fatal 4xx and kill the run. The model is gone, so the pool stops
        # offering it and the loop tries the next member instead -- no cooldown,
        # because a cooldown is a wait and there is nothing to wait for.
        if is_decommissioned(exc):
            provider.retire(detail or repr(exc))
            return True

        # Same shape as a retirement, one level up: the *key* is dead, so every
        # member drawing on that account is dead with it, and benching them one
        # 401 at a time would spend a failed call on each of the nine models the
        # account serves. Checked before `not transient`, which would call this
        # a fatal 4xx and end a run that still had six working accounts under it
        # (llm_router/base_provider.py#is_unauthorized).
        if is_unauthorized(exc):
            self._retire_account(provider, detail or repr(exc))
            return True

        if not transient:
            logger.error(f"{provider.name} failed with a non-transient error: {exc!r}")
            return False

        reason = detail or repr(exc)
        logger.warning(f"{provider.name} transient failure; rerouting. {reason}")
        if detail and run_manager is not None:
            run_manager.on_text(f"\n[router] {provider.name} failed ({provider.model}): "
                                f"{detail}\n")
        # A 503 is the model's capacity, not this key's: every account would
        # answer the same, so the next attempt should be a different model.
        if is_model_unavailable(exc):
            self._bench_model(provider)
            return True
        window = diagnostics.get("quota_window")
        if window in {"rpm", "tpm", "rpd"}:
            reset_wait = math.ceil(
                resets_in(window, getattr(provider, "platform", ""), time.time()))
            # A daily refusal is definitive until the vendor day turns even if
            # an accompanying generic RetryInfo suggests trying sooner. For a
            # minute bucket, RetryInfo is the provider's more precise answer.
            retry_after = (max(retry_after or 0, reset_wait) if window == "rpd"
                           else retry_after or reset_wait)
        provider.trigger_cooldown(retry_after)
        return True

    def _wait_for_cooldown(self) -> bool:
        """Sleep until the soonest account leaves cooldown. Returns False when
        nothing is coming back (no account in cooldown to wait for)."""
        # Asked with the same floor selection used, or the two disagree and the
        # loop gives up while a wide member is seconds from returning.
        wait = self.router.seconds_until_available(
            self.min_context if self.strict_context else None)
        if wait is None:
            return False
        delay = min(wait, _MAX_WAIT_SECONDS)
        logger.info(f"Whole pool in cooldown; waiting {delay:.0f}s for the next account.")
        time.sleep(delay)
        return True
