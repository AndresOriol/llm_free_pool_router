"""What the pool has actually consumed, written down.

The router knows which account served every call, and nothing was recording it
-- so "how much of the free tier is left today" could only be answered by
logging into each provider's console, one account at a time. This is the ledger
behind that question, and the quota panel ([quota/](quota/)) is a reader of it.

Two files under `llm_router/.usage/` (gitignored; `LLM_ROUTER_USAGE_DIR`
overrides the directory):

    ledger.jsonl   one JSON object per attempt against a provider
    pool.json      the pool as configured, with each model's declared limits

They are separate because they move on different clocks: the ledger only ever
grows, while the snapshot is rewritten from config every time the pool loads, so
it cannot go stale behind an edited `config.yaml`. The panel joins them on
`provider`.

A ledger line:

    {"ts": 1756..., "at": "2026-08-26T09:46:52+02:00",
     "provider": "GptOss120b_groq_1", "account": "groq_1",
     "platform": "groq", "model": "openai/gpt-oss-120b",
     "tokens_in": 812, "tokens_out": 96, "outcome": "ok",
     "request_id": "...", "attempt": 1, "estimated_tokens": 790}

`ts` is **when the request was issued**, which is the instant the vendor meters
it against; `at` is the same instant in local time, for reading the file by eye.

That distinction is the whole of `started`. `record` is only reachable once the
provider has answered, so writing down "now" recorded the moment the reply
arrived -- a call taking thirty seconds landed thirty seconds after the window
it was actually charged to. The daily windows never noticed. The per-minute ones
did: a burst smeared across the boundary and a single minute could read far
above the declared ceiling. Eleven requests in one clock minute against a
published five were read off this file and were an artifact of that line, not a
stale limit -- they dissolved when the same span was read over five minutes.

So the caller passes the moment it made the request and `record` prefers it.
Callers that cannot say fall back to now, which is the old behaviour and still
right for anything instantaneous.

**Lines written before this carry the completion time.** Nothing rewrites them,
so a ledger spanning the change is mixed, and a per-minute figure over its older
half is still smeared. Days are unaffected either way, which is the only window
[budget.py](quota/budget.py) routes on.

`tokens_in`/`tokens_out` are absent when the attempt was refused -- no tokens
were spent -- and a refusal carries `retry_after` instead when the provider
sent one. `reached: false` marks an attempt that never got an answer at all,
which spent nothing and is left out of the panel's request counts.

Every routed call also carries a random `request_id`, its one-based `attempt`
within that failover chain, and the pre-call `estimated_tokens`. Failed rows add
only structured, non-sensitive diagnostics: exception/error kind, numeric or
provider status, and any named quota metric, id and value. Raw exception text is
not persisted because tool failures may contain generated output and provider
messages may echo request material.

**A provider the router skipped is not here.** Selection filters a member out
before any call is made -- for size, or because this ledger says its day is
spent (`get_best_provider`) -- and nothing is recorded for a member that was
never asked: the ledger holds attempts, not intentions. That the daily filter
reads what this file writes is the one loop in the system, and it is a benign
one: skipping a member appends nothing, so the count it reads can only be moved
by an attempt that really happened.

`outcome` is `ok`, `rate_limited` or `error`. A refused attempt is recorded
rather than dropped: it still spent a request against the account's budget, and
how often an account is being turned away is the most useful thing the panel
has to say about a free tier.

**Recording can never fail a run.** An unattended agent losing hours of work
because a disk filled up would be a self-inflicted wound, and this ledger is a
convenience, not part of serving a request. Every entry point here swallows its
own errors and logs at debug.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional, Tuple

logger = logging.getLogger("LLMRouter")

_PACKAGE_DIR = Path(__file__).resolve().parent
_ENV_VAR = "LLM_ROUTER_USAGE_DIR"

_lock = threading.Lock()


def usage_dir() -> Path:
    """Where the ledger and the pool snapshot live."""
    override = os.environ.get(_ENV_VAR)
    return Path(override) if override else _PACKAGE_DIR / ".usage"


def ledger_path() -> Path:
    return usage_dir() / "ledger.jsonl"


def pool_path() -> Path:
    return usage_dir() / "pool.json"


def _message_tokens(message: Any) -> Tuple[Optional[int], Optional[int]]:
    """(tokens_in, tokens_out) off a provider's reply.

    Read from the provider's own count, never estimated: `estimate_tokens` is a
    chars/4 heuristic for routing, and a panel that reported a guess as
    consumption would be worse than reporting nothing.
    """
    usage = getattr(message, "usage_metadata", None)
    if isinstance(usage, dict):
        return usage.get("input_tokens"), usage.get("output_tokens")
    metadata = getattr(message, "response_metadata", None) or {}
    usage = metadata.get("token_usage") or {}
    return usage.get("prompt_tokens"), usage.get("completion_tokens")


def record(provider: Any, tokens_in: Optional[int] = None,
           tokens_out: Optional[int] = None, outcome: str = "ok",
           retry_after: Optional[int] = None, reached: bool = True,
           started: Optional[float] = None, request_id: Optional[str] = None,
           attempt: Optional[int] = None, estimated_tokens: Optional[int] = None,
           diagnostics: Optional[dict] = None) -> None:
    """Append one attempt against `provider` to the ledger.

    `started` is when the request was *issued*, and becomes the line's `ts`.
    Every caller here is on the far side of the provider's answer, so without it
    the ledger dates an attempt by when it came back rather than by when the
    vendor began charging for it -- which puts a slow call in the wrong minute.
    Omitted, it falls back to now: right for an attempt that failed before it
    left, and the behaviour every line written before this had.

    `retry_after` is the provider's own Retry-After, when it sent one with a
    refusal. It is the only statement about when a window clears that does not
    come from our own arithmetic, so it is kept verbatim.

    `reached` is False when the attempt never got an answer -- a timeout, a
    connection that failed. It is recorded because it happened, and marked
    because it cost the account nothing, so the panel can leave it out of the
    request count.
    """
    issued = time.time() if started is None else float(started)
    entry = {
        "ts": round(issued, 3),
        # The same instant, in the timezone of whoever is reading the file.
        # Every window in the panel is computed from `ts`; this is here so a
        # human scanning the ledger can tell which run a line belongs to
        # without converting epoch seconds in their head.
        "at": datetime.fromtimestamp(issued).astimezone().isoformat(timespec="seconds"),
        "provider": getattr(provider, "name", "unknown"),
        "account": getattr(provider, "account", "") or "unknown",
        "platform": getattr(provider, "platform", "") or "unknown",
        "model": getattr(provider, "model", "") or "unknown",
        "outcome": outcome,
    }
    if tokens_in is not None:
        entry["tokens_in"] = int(tokens_in)
    if tokens_out is not None:
        entry["tokens_out"] = int(tokens_out)
    if retry_after is not None:
        entry["retry_after"] = int(retry_after)
    if not reached:
        entry["reached"] = False
    if request_id:
        entry["request_id"] = request_id
    if attempt is not None:
        entry["attempt"] = int(attempt)
    if estimated_tokens is not None:
        entry["estimated_tokens"] = int(estimated_tokens)
    if diagnostics:
        entry.update(diagnostics)
    _append(entry)


def record_call(provider: Any, message: Any, outcome: str = "ok",
                started: Optional[float] = None, **context: Any) -> None:
    """Record a served call, reading the token counts off the reply.

    `started` is the moment the request went out; see `record`. A served call is
    the one that most needs it, because it is the one that took time.
    """
    tokens_in, tokens_out = _message_tokens(message)
    record(provider, tokens_in, tokens_out, outcome, started=started, **context)


def _append(entry: dict) -> None:
    """One line, one open/close.

    Same discipline as the eval trace: a run killed on a timeout keeps every
    line it had written, and a buffered handle would lose exactly the tail that
    explains why the account ran out.
    """
    try:
        path = ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, default=str)
        with _lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception as exc:  # noqa: BLE001 - the ledger must never fail a run
        logger.debug(f"Could not append to the usage ledger: {exc!r}")


def write_pool_snapshot(entries: Iterable[dict],
                        config_path: Optional[Any] = None) -> None:
    """Rewrite the pool snapshot the panel reads its limits from.

    Written by the loader on every pool build rather than maintained by hand,
    so the limits the panel measures against are always the ones the running
    router was configured with.
    """
    try:
        path = pool_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated": round(time.time(), 3),
            "config": str(config_path) if config_path else None,
            "pool": list(entries),
        }
        with _lock:
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - see _append
        logger.debug(f"Could not write the pool snapshot: {exc!r}")
