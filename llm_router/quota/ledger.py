"""Reading the two files under `llm_router/.usage/`.

Both are written by [usage.py](../usage.py) as the router runs. Nothing here
calls a provider, and nothing here touches the network at all.

    ledger.jsonl   one line per attempt this router made
    pool.json      the pool as configured, with each model's declared limits

Every reader here is forgiving: a missing file is an empty result, and a torn
last line is skipped rather than raised. The eval runner kills runs on a
timeout, so a half-written line is the expected shape of the most interesting
records, not a corruption to complain about.
"""

import json
from pathlib import Path
from typing import Any, List, Optional

from .. import usage


def usage_dir(override: Optional[str] = None) -> Path:
    """Where the three files live. `override` beats the env var and the default."""
    return Path(override) if override else usage.usage_dir()


def read_ledger(directory: Path) -> List[dict]:
    path = Path(directory) / "ledger.jsonl"
    if not path.exists():
        return []

    calls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            calls.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a killed writer's last line
    return calls


def _read_json(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def read_pool(directory: Path) -> Optional[dict]:
    return _read_json(Path(directory) / "pool.json")
