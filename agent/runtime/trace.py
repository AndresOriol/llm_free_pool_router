"""Local JSONL trace of one agent run, for offline evaluation.

Hosted traces expire; the eval harness (docs/10-metrics.md) derives every automatic
metric -- provider calls, failover bounces, tokens, bad tool calls, the failure
taxonomy -- from this file instead, so the evidence behind a verdict survives.

Enabled only when EVAL_TRACE_FILE is set. Unset, nothing is attached and the
agent runs exactly as before.

The handler is registered as an inheritable callback on the run config, so it
also sees the *underlying* provider models the router delegates to, not just
the RouterChatModel wrapper. That's what makes failover visible here: one
`llm_start` per attempt, and an `llm_error` for each attempt that got rerouted.
Router-level events carry model `router` and are excluded when counting real
provider calls.

One JSON object per line:

    {"ts": 1753790000.1, "event": "tool_end", "run_id": "...", "tool": "edit_file",
     "ok": true, "output": "..."}
"""

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

from langchain_core.callbacks import BaseCallbackHandler

_ENV_VAR = "EVAL_TRACE_FILE"

# Tool args and outputs carry whole files; a run would otherwise write hundreds
# of MB.
_MAX_FIELD = 2000
# ...but not everything the metrics need sits at the front. A `web_search`
# result ends with its `Sources:` block, and whether a search came back with
# sources or with the model's own recollection is the single fact that decides
# whether a research note is grounded. That tool is gone -- the search now
# returns the page itself (agent/runtime/web.py) -- and the tail
# still earns its place: a `write_file` carries its path after the content.
#
# Head-only clipping cost a real post-mortem: every recorded search looked
# source-less, because 2,000 characters ran out before the citations. Keeping a
# tail costs a few hundred bytes a call and makes the question answerable.
_TAIL_FIELD = 600


def _clip(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    if len(text) <= _MAX_FIELD + _TAIL_FIELD:
        return text
    dropped = len(text) - _MAX_FIELD - _TAIL_FIELD
    # The total is still named, as it was when this clipped the head only: a
    # reader has to be able to tell a 3,000-character result from a 300,000-one.
    return (text[:_MAX_FIELD]
            + f" ...[{len(text)} chars, {dropped} elided]... "
            + text[-_TAIL_FIELD:])


def _model_name(serialized, metadata, invocation_params) -> str:
    """Best-effort model id across providers, which each expose it elsewhere."""
    for source, key in ((metadata, "ls_model_name"),
                        (invocation_params, "model"),
                        (invocation_params, "model_name"),
                        (serialized, "name")):
        if isinstance(source, dict):
            value = source.get(key)
            if value:
                return str(value)
    if isinstance(serialized, dict) and serialized.get("id"):
        return str(serialized["id"][-1])
    return "unknown"


def _reply(response) -> Optional[str]:
    """The model's text, clipped. Separates "the model wrote nonsense" from
    "the parser mangled it" -- indistinguishable once protocol.py has run, and
    one recorded failure class (`<think>` blocks landing in findings) is only
    visible here, at the provider that emitted them.
    """
    try:
        return _clip(response.generations[0][0].text) or None
    except (AttributeError, IndexError, TypeError):
        return None


def _usage(response) -> tuple[Optional[int], Optional[int]]:
    """(tokens_in, tokens_out) -- usage_metadata first, then llm_output."""
    try:
        usage = response.generations[0][0].message.usage_metadata
        if usage:
            return usage.get("input_tokens"), usage.get("output_tokens")
    except (AttributeError, IndexError, TypeError):
        pass
    usage = (getattr(response, "llm_output", None) or {}).get("token_usage") or {}
    return usage.get("prompt_tokens"), usage.get("completion_tokens")


class JsonlTracer(BaseCallbackHandler):
    """Appends one JSON line per LLM/tool event.

    Reopens the file per line rather than holding a handle: the eval runner
    kills runs that exceed their timeout, and a buffered handle would lose the
    trace of exactly the runs that are most interesting to diagnose.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # run_id -> tool name, so tool_end/tool_error name their tool instead of
        # forcing the analysis to join on run_id.
        self._tools: dict[str, str] = {}

    def _write(self, event: str, run_id=None, **fields) -> None:
        record = {"ts": round(time.time(), 3), "event": event}
        if run_id is not None:
            record["run_id"] = str(run_id)
        record.update({k: v for k, v in fields.items() if v is not None})
        line = json.dumps(record, default=str)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    # -- LLM ---------------------------------------------------------------

    def on_chat_model_start(self, serialized, messages, *, run_id=None,
                            parent_run_id=None, metadata=None, **kwargs):
        self._write("llm_start", run_id=run_id,
                    parent_run_id=str(parent_run_id) if parent_run_id else None,
                    model=_model_name(serialized, metadata,
                                      kwargs.get("invocation_params")))

    def on_llm_end(self, response, *, run_id=None, **kwargs):
        tokens_in, tokens_out = _usage(response)
        self._write("llm_end", run_id=run_id, ok=True,
                    tokens_in=tokens_in, tokens_out=tokens_out,
                    text=_reply(response))

    def on_llm_error(self, error, *, run_id=None, **kwargs):
        # One of these per rerouted attempt: the failover-bounce count.
        self._write("llm_error", run_id=run_id, ok=False, detail=_clip(repr(error)))

    # -- Tools -------------------------------------------------------------

    def on_tool_start(self, serialized, input_str, *, run_id=None,
                      parent_run_id=None, **kwargs):
        name = (serialized or {}).get("name", "unknown")
        self._tools[str(run_id)] = name
        self._write("tool_start", run_id=run_id,
                    parent_run_id=str(parent_run_id) if parent_run_id else None,
                    tool=name, args=_clip(input_str))

    def on_tool_end(self, output, *, run_id=None, **kwargs):
        self._write("tool_end", run_id=run_id, ok=True,
                    tool=self._tools.pop(str(run_id), None), output=_clip(output))

    def on_tool_error(self, error, *, run_id=None, **kwargs):
        self._write("tool_error", run_id=run_id, ok=False,
                    tool=self._tools.pop(str(run_id), None), detail=_clip(repr(error)))


def tracer_from_env() -> Optional[JsonlTracer]:
    """A tracer if EVAL_TRACE_FILE is set, else None (tracing off)."""
    path = os.environ.get(_ENV_VAR)
    return JsonlTracer(path) if path else None
