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
     "tokens_in": 812, "tokens_out": 96, "outcome": "ok"}

`ts` is what the panel measures windows with; `at` is the same instant written
out in local time, for reading the file by eye.

**Known blind spot: `ts` is when the attempt finished, not when it started.**
`record` runs once the provider has answered, so a call that took thirty seconds
is written down thirty seconds after the vendor began charging for it. The daily
windows do not notice. The per-minute ones do: consumption lands in the clock
minute the reply arrived in rather than the one it was metered against, so a
burst smears across the boundary and a single minute can read far above the
declared ceiling. Peaks of eleven requests in one minute against a published
five have been read off this file, and they dissolve when the same span is read
over five minutes -- they are an artifact of this line, not a stale limit.

No reader can undo it, so [quota/windows.py](quota/windows.py) does not try and
[14.5](../docs/14-quota-panel.md#145-windows-and-when-they-reset) says so out
loud instead. The fix belongs here: take the issue time in `record` and let the
caller pass the moment it made the request, rather than the moment it got an
answer. Until then, do not conclude a per-minute limit is wrong from one minute
of this ledger.

`tokens_in`/`tokens_out` are absent when the attempt was refused -- no tokens
were spent -- and a refusal carries `retry_after` instead when the provider
sent one. `reached: false` marks an attempt that never got an answer at all,
which spent nothing and is left out of the panel's request counts.

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
           retry_after: Optional[int] = None, reached: bool = True) -> None:
    """Append one attempt against `provider` to the ledger.

    `retry_after` is the provider's own Retry-After, when it sent one with a
    refusal. It is the only statement about when a window clears that does not
    come from our own arithmetic, so it is kept verbatim.

    `reached` is False when the attempt never got an answer -- a timeout, a
    connection that failed. It is recorded because it happened, and marked
    because it cost the account nothing, so the panel can leave it out of the
    request count.
    """
    now = time.time()
    entry = {
        "ts": round(now, 3),
        # The same instant, in the timezone of whoever is reading the file.
        # Every window in the panel is computed from `ts`; this is here so a
        # human scanning the ledger can tell which run a line belongs to
        # without converting epoch seconds in their head.
        "at": datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds"),
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
    _append(entry)


def record_call(provider: Any, message: Any, outcome: str = "ok") -> None:
    """Record a served call, reading the token counts off the reply."""
    tokens_in, tokens_out = _message_tokens(message)
    record(provider, tokens_in, tokens_out, outcome)


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
