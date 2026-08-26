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

**This talks to the v2 API.** The v1 endpoints this used to call --
`GET /runs/{run_id}` via `Client.read_run`, and `POST /runs/query` via the
`load_child_runs=True` flag -- are removed on **2027-01-31**. The replacement is
`Client.traces.list_runs`, and it changes three things:

- **It keys off the trace id, not a run id, and that dissolves the old trap.**
  The previous fetch had to climb to the root, because the local collector
  appends in completion order and asking v1 for a leaf returned a valid one-span
  tree that looked like a working trace until you counted the spans. A trace id
  names the whole trace by definition, so there is nothing left to climb.
- **`project_id` is required.** The collector already knows it -- a `RunTree`
  carries it as `session_id` -- so the common path still needs no lookup;
  `read_project` is the fallback for when it does not.
- **Fields are opt-in.** v1 returned whole runs; v2 returns `id` alone unless
  asked otherwise. `_SELECTS` therefore names every field the enum offers,
  which keeps the old promise: everything LangSmith returns is kept, and
  deciding which fields are surplus stays a question to answer against a real
  tree rather than in advance.

The tree arrives flat, so the nesting is rebuilt here from `parent_run_ids`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("harness.trace")

# Ingestion is asynchronous: the run finishes locally before the tracer's
# background queue has flushed it. Ask, and retry a few times if it is not there
# yet, rather than racing it and recording nothing. Under v1 "not there yet"
# arrived as a 404; under v2 the trace comes back with no runs in it, so an
# empty result is a retry and not an answer.
_POLL_ATTEMPTS = 6
_POLL_SECONDS = 2.0

# Every field `RunSelectField` offers. Listed rather than derived: the enum
# lives under `langsmith._openapi_client`, and reaching into a vendor's private
# module to stay in sync is a worse failure than adding a line when it grows.
_SELECTS = [
    "ID", "NAME", "RUN_TYPE", "STATUS", "START_TIME", "END_TIME",
    "LATENCY_SECONDS", "FIRST_TOKEN_TIME", "ERROR", "ERROR_PREVIEW", "EXTRA",
    "METADATA", "EVENTS", "INPUTS", "INPUTS_PREVIEW", "OUTPUTS",
    "OUTPUTS_PREVIEW", "MANIFEST", "PARENT_RUN_IDS", "PROJECT_ID", "TRACE_ID",
    "THREAD_ID", "DOTTED_ORDER", "IS_ROOT", "REFERENCE_EXAMPLE_ID",
    "REFERENCE_DATASET_ID", "TOTAL_TOKENS", "PROMPT_TOKENS",
    "COMPLETION_TOKENS", "TOTAL_COST", "PROMPT_COST", "COMPLETION_COST",
    "PROMPT_TOKEN_DETAILS", "COMPLETION_TOKEN_DETAILS", "PROMPT_COST_DETAILS",
    "COMPLETION_COST_DETAILS", "PRICE_MODEL_ID", "TAGS", "APP_PATH",
    "ATTACHMENTS", "THREAD_EVALUATION_TIME", "IS_IN_DATASET", "SHARE_URL",
    "FEEDBACK_STATS",
]


def tracing_enabled() -> bool:
    """Whether a tree can be fetched at all. Both halves are required: the key
    authenticates the fetch, the flag is what makes the run get sent."""
    on = (os.environ.get("LANGSMITH_TRACING") or
          os.environ.get("LANGCHAIN_TRACING_V2") or "").lower() in ("1", "true")
    key = bool(os.environ.get("LANGSMITH_API_KEY") or
               os.environ.get("LANGCHAIN_API_KEY"))
    return on and key


def _as_dict(run: Any) -> dict:
    """One v2 run, as plain JSON. It is a pydantic model, so it knows how."""
    try:
        return run.model_dump(mode="json", exclude_none=True)
    except Exception:  # noqa: BLE001 - schema changed, or a field won't encode
        return json.loads(json.dumps(dict(run), default=str))


def nest(runs: list) -> Optional[dict]:
    """The trace's runs, flat and in start_time order, as one nested object.

    `parent_run_ids` is the ancestor chain root-first, so the immediate parent
    is its last entry. Children are appended in the order they arrive, which is
    start_time order, so the tree reads chronologically at every level.

    A run whose parent is missing from the batch is treated as a root rather
    than dropped: losing a span silently is the failure this whole module is
    trying not to have. If that leaves more than one root, the one the trace is
    named after wins and the rest hang off it.
    """
    if not runs:
        return None

    by_id: dict[str, dict] = {}
    for run in runs:
        data = _as_dict(run)
        data.pop("child_runs", None)
        by_id[str(data.get("id"))] = data

    roots: list[dict] = []
    for data in by_id.values():
        ancestors = data.get("parent_run_ids") or []
        parent = by_id.get(str(ancestors[-1])) if ancestors else None
        if parent is None or parent is data:
            roots.append(data)
        else:
            parent.setdefault("child_runs", []).append(data)

    if not roots:
        return None
    if len(roots) == 1:
        return roots[0]

    named = next((r for r in roots
                  if str(r.get("id")) == str(r.get("trace_id"))), roots[0])
    for orphan in roots:
        if orphan is not named:
            named.setdefault("child_runs", []).append(orphan)
    logger.warning(f"Trace had {len(roots)} roots; kept {named.get('id')} as "
                   f"the root and hung the rest off it.")
    return named


async def _list_runs(client, trace_id: str, project_id: str) -> Optional[dict]:
    """Poll until the trace has been ingested, then nest what came back."""
    for attempt in range(_POLL_ATTEMPTS):
        try:
            response = await client.traces.list_runs(
                trace_id, project_id=project_id, selects=_SELECTS)
            tree = nest(response.items or [])
            if tree is not None:
                return tree
        except Exception as exc:  # noqa: BLE001 - offline, or backend too old
            if attempt == _POLL_ATTEMPTS - 1:
                logger.warning(f"Could not read trace {trace_id} from "
                               f"LangSmith: {exc!r}")
                return None
        if attempt < _POLL_ATTEMPTS - 1:
            await asyncio.sleep(_POLL_SECONDS)
    logger.warning(f"Trace {trace_id} was still empty after {_POLL_ATTEMPTS} "
                   f"attempts; no trace recorded.")
    return None


def _await(coro):
    """Drive one coroutine from sync code.

    `Client.traces` is async-only, and this is called from `run_session`, which
    is sync. `asyncio.run` covers that. The thread is for the other case -- a
    caller that already has a loop running -- because raising there would lose
    the record for no reason other than which context asked for it.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def resolve_project_id(project_id: Optional[str] = None) -> Optional[str]:
    """The project the trace lives in, looked up by name when not already known.

    `traces.list_runs` requires it and the collector does not supply it, so this
    is the common path rather than the fallback. Returned rather than resolved
    privately inside the fetch so the caller can record the id it actually
    queried: a run record naming one project while the request used another is
    a record that cannot be checked.
    """
    if project_id:
        return str(project_id)
    if not tracing_enabled():
        return None
    try:
        from langsmith import Client
    except ImportError:
        logger.warning("langsmith is not installed; no trace recorded.")
        return None

    name = (os.environ.get("LANGSMITH_PROJECT") or
            os.environ.get("LANGCHAIN_PROJECT") or "default")
    try:
        return str(Client().read_project(project_name=name).id)
    except Exception as exc:  # noqa: BLE001 - no such project, or offline
        logger.warning(f"Could not resolve LangSmith project {name!r}, so no "
                       f"trace recorded: {exc!r}")
        return None


def fetch_tree(trace_id: str, project_id: Optional[str] = None) -> Optional[dict]:
    """The run tree from LangSmith, or None if it can't be had.

    Never raises. Losing the record is bad; losing the *run* because recording
    it failed afterwards is worse, and this is called after the work is done.

    `project_id` comes from the collector, which reads it off the `RunTree` as
    `session_id`. When it is absent the project is looked up by name, which is
    the one case that costs an extra round trip.
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

    project_id = resolve_project_id(project_id)
    if not project_id:
        return None

    return _await(_list_runs(Client(), str(trace_id), str(project_id)))


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
