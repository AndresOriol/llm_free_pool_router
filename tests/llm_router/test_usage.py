"""Checks for the usage ledger -- the file the quota panel reads, so its shape
is a contract in the same way the eval trace's is (docs/07-observability.md).

No framework: `python -m tests.llm_router.test_usage` (or run the file).
"""

import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from llm_router import usage
from llm_router.loader import load_providers_from_config


class _Provider:
    """Stand-in for a pool member: only what the ledger reads off one."""

    def __init__(self):
        self.name = "GptOss120b_groq_1"
        self.account = "groq_1"
        self.platform = "groq"
        self.model = "openai/gpt-oss-120b"


def _message(usage_metadata=None, response_metadata=None):
    return type("M", (), {"usage_metadata": usage_metadata,
                          "response_metadata": response_metadata or {}})()


def _lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


_CONFIG = """
accounts:
  - name: groq_1
    platform: groq
    type: openai_compatible
    url: https://api.groq.com/openai/v1
    api_key_env: TEST_QUOTA_KEY
models:
  - name: GptOss120b
    platform: groq
    model: openai/gpt-oss-120b
    priority: 2
    max_input_tokens: 8000
    limits: {rpm: 30, tpm: 8000, rpd: 1000, tpd: 100000}
"""


def _check_loader_snapshot():
    """The snapshot is written by the loader, from the pool it actually built.

    That is the join the panel depends on: a member's limits are found by the
    same `provider` name the ledger records, so the two files have to be written
    from one source or the panel silently measures usage against nothing.
    """
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["LLM_ROUTER_USAGE_DIR"] = tmp
        os.environ["TEST_QUOTA_KEY"] = "not-a-real-key"
        config = Path(tmp) / "config.yaml"
        config.write_text(_CONFIG, encoding="utf-8")

        providers = load_providers_from_config(config)
        assert len(providers) == 1, providers
        assert providers[0].platform == "groq", providers[0].platform
        assert providers[0].account == "groq_1", providers[0].account

        snapshot = json.loads((Path(tmp) / "pool.json").read_text(encoding="utf-8"))
        member = snapshot["pool"][0]
        assert member["provider"] == providers[0].name == "GptOss120b_groq_1", member
        assert member["limits"] == {"rpm": 30, "tpm": 8000, "rpd": 1000, "tpd": 100000}, member
        del os.environ["TEST_QUOTA_KEY"]


def _check_issue_time(ledger):
    """`ts` dates an attempt by when it was issued, not by when it came back.

    Everything that records is on the far side of the provider's answer, so
    without this a call taking half a minute lands in a minute the vendor never
    charged it to -- and the per-minute gauges read a burst that never happened
    (llm_router/quota/windows.py).
    """
    issued = time.time() - 40
    usage.record_call(_Provider(), _message({"input_tokens": 5, "output_tokens": 1}),
                      started=issued)
    usage.record(_Provider(), outcome="rate_limited", retry_after=7, started=issued)
    served, refused = _lines(ledger)[-2:]

    assert served["ts"] == round(issued, 3), served
    assert refused["ts"] == round(issued, 3), "a refusal was issued then too"
    # `at` is the same instant, so the two never disagree by the call's length.
    assert datetime.fromtimestamp(issued).astimezone().isoformat(
        timespec="seconds") == served["at"], served

    # Omitted, it still means now: right for an attempt that failed before it
    # left, and what every line written before this did.
    usage.record(_Provider(), outcome="error", reached=False)
    assert _lines(ledger)[-1]["ts"] >= issued + 39


def _run():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["LLM_ROUTER_USAGE_DIR"] = tmp
        ledger = Path(tmp) / "ledger.jsonl"

        usage.record(_Provider(), tokens_in=812, tokens_out=96)
        usage.record(_Provider(), outcome="rate_limited", retry_after=33,
                     request_id="request-1", attempt=2, estimated_tokens=7000,
                     duration_ms=1234.56,
                     diagnostics={"error_type": "QuotaError",
                                  "error_kind": "rate_limit",
                                  "quota_metric": "input_tokens_per_minute"})

        served, refused = _lines(ledger)
        assert served["provider"] == "GptOss120b_groq_1", served
        assert (served["account"], served["platform"]) == ("groq_1", "groq"), served
        assert served["model"] == "openai/gpt-oss-120b", served
        assert (served["tokens_in"], served["tokens_out"]) == (812, 96), served
        assert served["outcome"] == "ok", served
        assert isinstance(served["ts"], float), served
        # A refused attempt is recorded -- it still spent a request -- but has
        # no token counts to report.
        assert refused["outcome"] == "rate_limited", refused
        assert "tokens_in" not in refused, refused
        # The provider's own answer to "when can I come back", kept verbatim --
        # the panel would otherwise have to infer it (llm_router/quota).
        assert refused["retry_after"] == 33, refused
        assert refused["request_id"] == "request-1", refused
        assert refused["attempt"] == 2, refused
        assert refused["estimated_tokens"] == 7000, refused
        assert refused["duration_ms"] == 1234.6, refused
        assert refused["error_type"] == "QuotaError", refused
        assert refused["error_kind"] == "rate_limit", refused
        assert refused["quota_metric"] == "input_tokens_per_minute", refused

        # Token counts come from the provider's own numbers, wherever the
        # LangChain wrapper put them.
        usage.record_call(_Provider(), _message({"input_tokens": 10, "output_tokens": 3}))
        usage.record_call(_Provider(), _message(
            response_metadata={"token_usage": {"prompt_tokens": 7, "completion_tokens": 2}}))
        modern, legacy = _lines(ledger)[2:]
        assert (modern["tokens_in"], modern["tokens_out"]) == (10, 3), modern
        assert (legacy["tokens_in"], legacy["tokens_out"]) == (7, 2), legacy

        # A reply that reports nothing is still an attempt worth counting.
        usage.record_call(_Provider(), _message())
        silent = _lines(ledger)[4]
        assert silent["outcome"] == "ok" and "tokens_in" not in silent, silent

        _check_issue_time(ledger)

        usage.write_pool_snapshot(
            [{"provider": "GptOss120b_groq_1", "limits": {"rpm": 30, "tpm": 8000}}],
            config_path="llm_router/config.yaml")
        snapshot = json.loads((Path(tmp) / "pool.json").read_text(encoding="utf-8"))
        assert snapshot["config"] == "llm_router/config.yaml", snapshot
        assert snapshot["pool"][0]["limits"]["rpm"] == 30, snapshot

        # Rewritten, not appended: the snapshot is the current pool, not a
        # history of pools.
        usage.write_pool_snapshot([], config_path=None)
        snapshot = json.loads((Path(tmp) / "pool.json").read_text(encoding="utf-8"))
        assert snapshot["pool"] == [], snapshot

    _check_loader_snapshot()

    # The ledger is a convenience; it must never take a run down with it. Point
    # it at a path that cannot be a directory and check both writers survive.
    with tempfile.NamedTemporaryFile(suffix=".not-a-dir", delete=False) as blocker:
        blocked = blocker.name
    os.environ["LLM_ROUTER_USAGE_DIR"] = str(Path(blocked) / "nested")
    usage.record(_Provider())
    usage.write_pool_snapshot([])
    os.unlink(blocked)
    del os.environ["LLM_ROUTER_USAGE_DIR"]

    print("usage: all checks passed")


if __name__ == "__main__":
    _run()
