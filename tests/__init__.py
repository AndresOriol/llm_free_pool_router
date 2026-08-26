"""Keep the test suite out of the pool's own usage history.

Anything that drives `RouterChatModel` records the attempt through
[usage.py](../llm_router/usage.py), which appends to
`llm_router/.usage/ledger.jsonl` unless `LLM_ROUTER_USAGE_DIR` says otherwise.
A test's fake members are not free-tier consumption, and the quota panel has no
way to tell them apart: it read them back as an "unknown" platform serving a
model called `m`, next to the real accounts.

Redirected here, on the package, rather than in a `conftest.py`, because these
files run two ways -- `python -m pytest` and `python tests/agent/test_x.py` --
and only this is on both paths. A test that wants to read back what it wrote
still sets the variable itself; that wins over this.
"""

import os
import tempfile

os.environ.setdefault("LLM_ROUTER_USAGE_DIR",
                      tempfile.mkdtemp(prefix="llm-router-tests-"))
