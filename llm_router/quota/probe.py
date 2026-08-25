"""Asking the vendor what is actually left, at the moment you ask.

The ledger counts what *this* router spent. The vendor counts what the account
spent, which is not the same number the moment anything else touches the key --
another machine, another checkout, a script run by hand. For the question the
panel exists to answer, the vendor's count is the authority.

**There is no usage endpoint to ask.** Probed on 2026-08-25: Groq's
`GET /models` carries no rate-limit headers, and neither Gemini endpoint carries
anything at all. Groq reports its budget only as headers on a real completion:

    x-ratelimit-limit-requests: 1000     x-ratelimit-remaining-requests: 999
    x-ratelimit-limit-tokens:  8000      x-ratelimit-remaining-tokens:  7927
    x-ratelimit-reset-requests: 1m26.4s  x-ratelimit-reset-tokens: 547ms

So a reading costs one request. This module buys it deliberately: a one-token
completion per pool member that can answer, about 70 tokens and one request each
against budgets of 8,000/minute and 1,000/day. It is never automatic -- `status`
and `panel` read the cached reading unless asked to refresh it, so an agent
polling the panel cannot quietly spend the budget it is polling about.

A 429 answers too: a refused call still carries the headers, and an account that
has hit its wall is exactly when the reading is worth having.

Google's free tier has no equivalent. Its quota is visible in the AI Studio
console and, programmatically, only through a GCP project's monitoring -- a
service account and a different auth story, not something an AI Studio key can
do. Gemini members are marked as unable to report rather than left looking
un-probed.
"""

import json
import logging
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional

from .. import usage

logger = logging.getLogger("LLMRouter")

# One token out, one word in: the cheapest thing that still returns the headers.
_PROBE_BODY = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 1}
_TIMEOUT_SECONDS = 20

_DURATION = re.compile(r"([\d.]+)(ms|s|m|h)")
_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}


def reset_seconds(text: Optional[str]) -> Optional[float]:
    """`"1m26.4s"` -> 86.4. Groq quotes resets in compound units, not seconds."""
    if not text:
        return None
    parts = _DURATION.findall(str(text))
    if not parts:
        return None
    return round(sum(float(value) * _UNITS[unit] for value, unit in parts), 3)


def _int_header(headers, name: str) -> Optional[int]:
    value = headers.get(name)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _reading(headers, status: int) -> dict:
    return {
        "ts": round(time.time(), 3),
        "status": status,
        "ok": True,
        "limit_requests": _int_header(headers, "x-ratelimit-limit-requests"),
        "remaining_requests": _int_header(headers, "x-ratelimit-remaining-requests"),
        "limit_tokens": _int_header(headers, "x-ratelimit-limit-tokens"),
        "remaining_tokens": _int_header(headers, "x-ratelimit-remaining-tokens"),
        "reset_requests": reset_seconds(headers.get("x-ratelimit-reset-requests")),
        "reset_tokens": reset_seconds(headers.get("x-ratelimit-reset-tokens")),
    }


def _probe_one(provider) -> dict:
    """One completion against one pool member, for its headers alone."""
    request = urllib.request.Request(
        provider.url.rstrip("/") + "/chat/completions",
        data=json.dumps({**_PROBE_BODY, "model": provider.model}).encode("utf-8"),
        headers={"Authorization": f"Bearer {provider.api_key}",
                 "Content-Type": "application/json",
                 # Groq sits behind Cloudflare, which answers 403 to urllib's
                 # default agent. Any honest name gets through.
                 "User-Agent": "free_coding_agent-quota"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return _reading(response.headers, response.status)
    except urllib.error.HTTPError as exc:
        # A refused call carries the same headers, and is the most interesting
        # moment to read them. Only a body-less failure is a failed probe.
        reading = _reading(exc.headers, exc.code)
        if reading["limit_requests"] is None and reading["limit_tokens"] is None:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:200]
            except Exception:  # noqa: BLE001 - a body is a bonus, not a promise
                pass
            return {"ts": round(time.time(), 3), "ok": False, "status": exc.code,
                    "error": f"HTTP {exc.code} {detail}".strip()}
        return reading
    except Exception as exc:  # noqa: BLE001 - a probe must not take the caller down
        return {"ts": round(time.time(), 3), "ok": False, "error": repr(exc)}


def probe_pool(providers: Optional[List] = None) -> dict:
    """Read every member that can report, and cache it in `vendor.json`.

    Returns the payload it wrote. Members whose platform sends no rate-limit
    headers are recorded as `reports: false` -- an honest "cannot know" reads
    differently on the panel from "not asked yet".
    """
    from ..loader import load_providers_from_config
    from ..providers import OpenAICompatibleProvider

    if providers is None:
        providers = load_providers_from_config()

    members = {}
    for provider in providers:
        if not isinstance(provider, OpenAICompatibleProvider):
            members[provider.name] = {
                "ts": round(time.time(), 3), "reports": False,
                "detail": f"{provider.platform} returns no rate-limit headers",
            }
            continue
        logger.info(f"Probing {provider.name} ({provider.model}).")
        members[provider.name] = {**_probe_one(provider), "reports": True}

    payload = {"probed": round(time.time(), 3), "members": members}
    path = Path(usage.usage_dir()) / "vendor.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def probe_cost(providers: List) -> int:
    """How many requests a probe would spend, for saying so before spending it."""
    from ..providers import OpenAICompatibleProvider

    return sum(1 for provider in providers
               if isinstance(provider, OpenAICompatibleProvider))
