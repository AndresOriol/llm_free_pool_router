"""The run's record: one nested object, fetched from LangSmith after the run.

This reverses a settled decision. The old record was composed locally out of
four artefacts -- a flat `trace.jsonl`, a `journal.jsonl`, one markdown file per
role turn, and a rationale -- because a hosted trace expires and a verdict must
rest on files on disk (docs/07-observability.md#71-why-two).

The expiry argument still holds; the *composition* was the mistake. Three of
those four files existed to record handoffs between narrow roles, and a
conversational harness has no handoffs to record. What is wanted instead is the
shape LangSmith already builds: one run, with children, each carrying its
inputs, outputs, timing, token counts and error. So this fetches that tree and
writes it down. Expiry is answered by the snapshot, not by rebuilding the tree
by hand from callbacks.

Two ids, and the difference matters:

- `collect_runs()` learns the **root run id** locally, from the same callback
  machinery LangSmith's tracer uses, so no network call is needed to find out
  what to ask for. It works whether or not tracing is on.
- `Client.read_run(..., load_child_runs=True)` is what actually returns the
  tree, and needs `LANGSMITH_TRACING=1` and `LANGSMITH_API_KEY`.

Everything LangSmith returns is kept. Deciding which fields are surplus is a
question to answer against a real tree, not in advance.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("harness.trace")

# Ingestion is asynchronous: the run finishes locally before the tracer's
# background queue has flushed it. Ask, and retry a few times if it is not there
# yet, rather than racing it and recording nothing.
_POLL_ATTEMPTS = 6
_POLL_SECONDS = 2.0


def tracing_enabled() -> bool:
    """Whether a tree can be fetched at all. Both halves are required: the key
    authenticates the fetch, the flag is what makes the run get sent."""
    on = (os.environ.get("LANGSMITH_TRACING") or
          os.environ.get("LANGCHAIN_TRACING_V2") or "").lower() in ("1", "true")
    key = bool(os.environ.get("LANGSMITH_API_KEY") or
               os.environ.get("LANGCHAIN_API_KEY"))
    return on and key


def _serialize(run: Any) -> dict:
    """One LangSmith run and its children, as plain JSON.

    `Run` is a pydantic model, so it knows how to do this; the recursion is only
    needed because `load_child_runs` hangs the children off an attribute that
    the parent's own dump does not descend into.
    """
    try:
        data = json.loads(run.json(exclude_none=True))
    except Exception:  # noqa: BLE001 - schema changed, or a field won't encode
        data = json.loads(json.dumps(dict(run), default=str))
    children = getattr(run, "child_runs", None) or []
    if children:
        data["child_runs"] = [_serialize(child) for child in children]
    return data


def fetch_tree(root_run_id: str) -> Optional[dict]:
    """The run tree from LangSmith, or None if it can't be had.

    Never raises. Losing the record is bad; losing the *run* because recording
    it failed afterwards is worse, and this is called after the work is done.
    """
    if not tracing_enabled():
        logger.warning(
            "No LangSmith trace recorded: set LANGSMITH_TRACING=1 and "
            "LANGSMITH_API_KEY to capture one. The run itself is unaffected.")
        return None

    try:
        from langsmith import Client
    except ImportError:
        logger.warning("langsmith is not installed; no trace recorded.")
        return None

    client = Client()
    for attempt in range(_POLL_ATTEMPTS):
        try:
            run = client.read_run(root_run_id, load_child_runs=True)
            return _serialize(run)
        except Exception as exc:  # noqa: BLE001 - not ingested yet, or offline
            if attempt == _POLL_ATTEMPTS - 1:
                logger.warning(f"Could not read run {root_run_id} from "
                               f"LangSmith: {exc!r}")
                return None
            time.sleep(_POLL_SECONDS)
    return None


def write(path: Path, tree: Optional[dict], meta: dict) -> Optional[Path]:
    """Write the tree plus what the fetch itself needs to be interpretable.

    `meta` is recorded even when the tree is None, so a run with no trace says
    so on disk instead of leaving an absent file to be explained later.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta, "trace": tree}
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path
