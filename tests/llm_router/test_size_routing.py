"""Checks for size-aware routing: estimate_tokens() and get_best_provider()
filtering by a request's estimated size.

No framework: `python -m tests.llm_router.test_size_routing` (or run the file).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from llm_router.base_provider import estimate_tokens
from llm_router.quota.budget import RpdBudget
from llm_router.router import AutonomousLLMRouter


class _FakeProvider:
    """Minimal stand-in exposing what get_best_provider() reads."""

    def __init__(self, name, priority, max_input_tokens, available=True):
        self.name = name
        self.priority = priority
        self.max_input_tokens = max_input_tokens
        self._available = available

    def check_availability(self):
        return self._available


def _run():
    # estimate_tokens: ~4 chars per token over message content (dict form).
    assert estimate_tokens([{"role": "user", "content": "x" * 40}]) == 10
    # Tool schemas count toward the estimate too.
    with_tools = estimate_tokens([{"content": "x" * 40}], tools=["y" * 40])
    assert with_tools == 20, with_tools
    # BaseMessage-like objects (content attribute) are handled.
    msg = type("M", (), {"content": "z" * 20})()
    assert estimate_tokens([msg]) == 5

    small = _FakeProvider("groq_small", priority=1, max_input_tokens=6000)
    mid = _FakeProvider("groq_mid", priority=5, max_input_tokens=30000)
    big = _FakeProvider("gemini_big", priority=8, max_input_tokens=250000)
    # Size only: the daily-quota filter is switched off so this stays a unit
    # test of the size rules rather than a reader of whatever the machine's real
    # ledger happens to hold (it is exercised in test_rpd_filter.py).
    router = AutonomousLLMRouter([small, mid, big], quota=RpdBudget(enabled=False))

    # No estimate -> pure priority, as before.
    assert router.get_best_provider() is small

    # A tiny request fits everything -> lowest priority (the fast small model).
    assert router.get_best_provider(3000) is small

    # 20k overflows the small model (6000*0.9) -> next fitting by priority.
    assert router.get_best_provider(20000) is mid

    # 100k fits only the big window.
    assert router.get_best_provider(100000) is big

    # Bigger than every window -> fall back to the largest, don't stall.
    assert router.get_best_provider(500000) is big

    # If the only fitting provider is in cooldown, it's skipped; nothing else
    # fits, so fall back to the largest *available* window.
    big._available = False
    assert router.get_best_provider(100000) is mid

    print("size_routing: all checks passed")


# pytest collects `test_*` functions, not `_run`. Without this the whole
# file was inert under `python -m pytest tests`: it had never run in CI,
# which is how a bug in the thing it checks survived having a test.
def test_size_routing():
    _run()


if __name__ == "__main__":
    _run()
