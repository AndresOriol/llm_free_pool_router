"""The run's record: the turns and tool calls, fetched from LangSmith after.

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
  asked otherwise, so `_SELECTS` names every field the enum offers. Fetching
  everything and writing a subset is deliberate: what is surplus is a question
  about a real tree, and answering it in the request would mean re-running the
  agent to change the answer.

The tree arrives flat, so the nesting is rebuilt here from `parent_run_ids` and
sorted back into `start_time` order.

**What reaches disk is condensed** -- see the note above `condense`. The fetched
tree is faithful and unreadable: 22 MB across 255 spans for a 17-turn run, 88%
of it the message history recopied at every level of the middleware tower. What
is written instead is the run someone would want to read -- one entry per model
call, holding the conversation as that call received it, what came back, and
what its tool calls then produced -- at about 1% of the size, with nothing a
turn did truncated.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
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
    is its last entry.

    Children are sorted by `start_time`, so the tree reads chronologically at
    every level. This is not decoration. `traces.list_runs` returns the batch
    newest-first, and appending in arrival order silently built every tree
    backwards -- turn 17 first, turn 1 last -- which reads as a plausible run
    right up until you notice the context shrinking instead of growing.

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
        return _sorted(roots[0])

    named = next((r for r in roots
                  if str(r.get("id")) == str(r.get("trace_id"))), roots[0])
    for orphan in roots:
        if orphan is not named:
            named.setdefault("child_runs", []).append(orphan)
    logger.warning(f"Trace had {len(roots)} roots; kept {named.get('id')} as "
                   f"the root and hung the rest off it.")
    return _sorted(named)


def _sorted(node: dict) -> dict:
    """One node's descendants, put back into start_time order."""
    children = node.get("child_runs")
    if children:
        children.sort(key=lambda c: str(c.get("start_time") or ""))
        for child in children:
            _sorted(child)
    return node


# ---------------------------------------------------------------------------
# Condensing: the tree as a run someone can read
# ---------------------------------------------------------------------------
#
# The fetched tree is faithful and unreadable. One real 17-turn run came to
# 22 MB across 255 spans, and 88% of that was `inputs` -- because every level of
# the six-deep middleware tower carries its own copy of the whole message
# history, and the history is replayed in full on every turn. The conversation
# underneath is a few hundred KB; the rest is the same text written back down a
# hundred-odd times.
#
# So the record keeps the run and drops the recording apparatus. Three moves:
#
# 1. **Only spans that did something survive.** `llm` spans are turns, `tool`
#    spans are tool calls. The `chain` spans -- 195 of the 255 -- are middleware
#    wrappers whose inputs and outputs are their child's, and carry nothing that
#    is not somewhere else.
# 2. **Tools are grouped under the turn that asked for them**, matched by
#    `tool_call_id`, instead of hanging off the graph as siblings of the model
#    call. A turn becomes what it actually is: the model spoke, then these tools
#    ran and returned this.
# 3. **One copy of the history per call, not eight.** The tower's copies are all
#    the same list, so the call's own `input` is the only one kept -- but it is
#    kept *whole*, on every turn. That is redundant across turns on purpose:
#    turn N's input is mostly turn N-1's, and paying ~200 KB for it is what
#    makes the file answer the question it exists for -- what did the model
#    actually see at the moment it went wrong. `context_rewritten` flags the
#    turns where that history stopped being the previous one extended, which
#    from outside is what summarization looks like.
#
# So each turn reads as `input` -> `output` -> `tool_results`: the conversation
# as that call received it, what came back, and what running its tool calls
# produced. Nothing a turn did is truncated. It is the within-turn duplication
# that goes, not the content.

_LANGSMITH_APP = "https://smith.langchain.com"

# The wrapper span the router puts around each provider attempt. Preferred as
# the turn's answer because its usage numbers cover the whole turn, including
# attempts that failed before one worked.
_ROUTER = "RouterChatModel"

# Stands in for the system prompt inside a turn's `input`.
_SAME_AS_SYSTEM = "<the system_prompt at the top of this file, verbatim>"


def _text(content: Any) -> str:
    """A message's content as plain text.

    Content is a string on some providers and a list of typed parts on others;
    both mean the same thing to a reader.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content
                       if isinstance(part, dict))
    return "" if content is None else str(content)


def _kwargs(message: Any) -> dict:
    """The payload of a serialised LangChain message.

    They arrive wrapped (`{"lc": 1, "type": "constructor", "kwargs": {...}}`)
    from an LLM span's inputs, and bare from a few other places.
    """
    if isinstance(message, dict):
        return message.get("kwargs") or message
    return {}


def _message(message: Any, system: Optional[str] = None) -> dict:
    """One message from a call's history, rendered.

    The system prompt is the one thing not written out here. It is identical on
    every turn and runs to 15 KB, so repeating it per turn would put a quarter
    of a megabyte of the same text in the file; it is written once at the top
    and stood in for here. A system message that does *not* match the hoisted
    one is written in full, because then it is news.
    """
    kw = _kwargs(message)
    text = _text(kw.get("content"))
    out = {"role": kw.get("type")}
    out["text"] = (_SAME_AS_SYSTEM
                   if kw.get("type") == "system" and text and text == system
                   else text)
    for field in ("name", "tool_call_id", "status"):
        if kw.get(field):
            out[field] = kw[field]
    if kw.get("tool_calls"):
        out["tool_calls"] = [{"id": c.get("id"), "name": c.get("name"),
                              "args": c.get("args")}
                             for c in kw["tool_calls"]]
    return out


def _history(span: dict) -> list:
    """The messages an LLM span was called with, unwrapped.

    `inputs.messages` is a list of *batches*; a chat model is invoked with one.
    """
    batches = (span.get("inputs") or {}).get("messages") or []
    if batches and isinstance(batches[0], list):
        return batches[0]
    return batches


def _fingerprint(messages: list) -> list:
    """Messages reduced to what identifies them, for comparing two histories.

    Enough to tell "the same conversation, extended" from "a different
    conversation" without holding the text twice.
    """
    return [(_kwargs(m).get("type"), _text(_kwargs(m).get("content"))[:200])
            for m in messages]


def _generation(span: dict) -> dict:
    """The message an LLM span produced, or an empty dict if it produced none."""
    for batch in (span.get("outputs") or {}).get("generations") or []:
        for item in batch or []:
            kw = _kwargs((item or {}).get("message"))
            if kw:
                return kw
    return {}


def _moment(stamp: str) -> datetime:
    """One LangSmith timestamp. They end in `Z`, which `fromisoformat` predates
    accepting on the Python versions this has to run on."""
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _seconds(span: dict) -> Optional[float]:
    latency = span.get("latency_seconds")
    return round(latency, 3) if isinstance(latency, (int, float)) else None


def _tokens(span: dict) -> dict:
    """A span's token counts, with the details that explain the size.

    `cache_read` is the number that makes a long run affordable and `reasoning`
    is output the run paid for but never sees, so both are worth naming rather
    than leaving folded into the totals.
    """
    out = {}
    for key, field in (("in", "prompt_tokens"), ("out", "completion_tokens"),
                       ("total", "total_tokens")):
        if span.get(field) is not None:
            out[key] = span[field]
    cache = (span.get("prompt_token_details") or {}).get("raw") or {}
    if cache.get("cache_read"):
        out["cache_read"] = cache["cache_read"]
    reasoning = (span.get("completion_token_details") or {}).get("raw") or {}
    if reasoning.get("reasoning"):
        out["reasoning"] = reasoning["reasoning"]
    return out


def _walk(node: dict):
    """Every span in the tree, parents before children."""
    yield node
    for child in node.get("child_runs") or []:
        yield from _walk(child)


def _tool_call(span: dict, call_id: Optional[str] = None) -> dict:
    """One tool span: what came back and how long it took.

    The arguments are not repeated here -- they are on the model's own
    `output.tool_calls`, which is where they came from, and `tool_call_id` links
    the two. An unclaimed span has no such entry to point at, so that one keeps
    its arguments.
    """
    result = (span.get("outputs") or {}).get("output")
    payload = result if isinstance(result, dict) else {}
    call = {"tool_call_id": call_id or payload.get("tool_call_id"),
            "name": span.get("name"),
            "status": payload.get("status") or span.get("status")}
    if call_id is None:
        call["args"] = span.get("inputs")
    seconds = _seconds(span)
    if seconds is not None:
        call["seconds"] = seconds
    call["output"] = _text(payload.get("content")) if payload else _text(result)
    if span.get("error"):
        call["error"] = span["error"]
    return call


def _attempt(span: dict) -> dict:
    """One provider call the router made, whether or not it worked."""
    meta = span.get("metadata") or {}
    out = {"model": meta.get("ls_model_name") or span.get("name"),
           "provider": meta.get("ls_provider"),
           "status": span.get("status")}
    seconds = _seconds(span)
    if seconds is not None:
        out["seconds"] = seconds
    if span.get("error"):
        out["error"] = span["error"]
    return out


def _turns(root: dict, system: Optional[str] = None) -> list:
    """The run as a list of turns, each with what entered the model, what came
    back, and what running its tool calls produced.

    LLM spans are grouped by parent, because the router's wrapper span and the
    provider attempts underneath it are siblings there -- one group is one turn,
    however many providers it took to get an answer out of the pool.
    """
    groups: dict = {}
    order: list = []
    tools: list = []
    for span in _walk(root):
        for child in span.get("child_runs") or []:
            if child.get("run_type") == "llm":
                key = str(span.get("id"))
                if key not in groups:
                    groups[key] = []
                    order.append(key)
                groups[key].append(child)
        if span.get("run_type") == "tool":
            tools.append(span)

    # Tool spans are siblings of the model call in the graph, so each is
    # attached to the turn whose reply asked for it, by id.
    by_call_id: dict = {}
    for span in tools:
        result = (span.get("outputs") or {}).get("output")
        call_id = result.get("tool_call_id") if isinstance(result, dict) else None
        by_call_id.setdefault(str(call_id), []).append(span)

    turns = []
    previous: Optional[list] = None
    for number, key in enumerate(order, start=1):
        spans = sorted(groups[key], key=lambda s: str(s.get("start_time") or ""))
        answered = ([s for s in spans if s.get("name") == _ROUTER] or
                    [s for s in spans if s.get("status") == "success"] or spans)
        span = answered[-1]
        attempts = [_attempt(s) for s in spans if s is not span]
        message = _generation(span)
        meta = span.get("metadata") or {}
        response = message.get("response_metadata") or {}
        history = _history(span)

        turn = {
            "n": number,
            "start": span.get("start_time"),
            "seconds": _seconds(span),
            "model": (response.get("model_name") or meta.get("ls_model_name") or
                      next((a["model"] for a in attempts
                            if a.get("status") == "success"),
                           attempts[0]["model"] if attempts else None)),
            "provider": response.get("model_provider") or meta.get("ls_provider"),
            "status": span.get("status"),
            "tokens": _tokens(span),
            # How much history the model was handed. Read down the column: it
            # should climb, and a drop dates a summarization.
            "context_messages": len(history),
            # Everything that entered the model on this call, in order.
            "input": [_message(m, system) for m in history],
        }

        fingerprint = _fingerprint(history)
        if previous is not None and fingerprint[:len(previous)] != previous:
            # This history is not the previous one extended, which from outside
            # is what summarization looks like. `input` above already holds what
            # the model saw; this is the flag that says where to look.
            turn["context_rewritten"] = True
        previous = fingerprint

        # What came back out. `text` is empty on a turn that only called tools,
        # which is most of them, so it is omitted rather than written as "".
        out: dict = {}
        if _text(message.get("content")):
            out["text"] = _text(message.get("content"))
        if message.get("tool_calls"):
            out["tool_calls"] = [{"id": c.get("id"), "name": c.get("name"),
                                  "args": c.get("args")}
                                 for c in message["tool_calls"]]
        if response.get("finish_reason"):
            out["finish_reason"] = response["finish_reason"]
        if span.get("error"):
            out["error"] = span["error"]
        turn["output"] = out

        # A lone successful attempt only restates the turn. Attempts are worth
        # recording when the pool had to work for the answer -- that is the
        # failover this whole project exists to do, and it is invisible
        # anywhere else in the record.
        if len(attempts) > 1 or any(a.get("status") == "error"
                                    for a in attempts):
            turn["attempts"] = attempts

        # Running the calls above. Kept apart from `output` because these did
        # not come out of the model, and keyed by id rather than repeating the
        # arguments already recorded there.
        results = []
        for call in message.get("tool_calls") or []:
            waiting = by_call_id.get(str(call.get("id")))
            if waiting:
                results.append(_tool_call(waiting.pop(0), call.get("id")))
            else:
                results.append({"tool_call_id": call.get("id"),
                                "name": call.get("name"),
                                "status": "no result recorded"})
        if results:
            turn["tool_results"] = results
        turns.append(turn)

    # A tool span nothing claimed is kept rather than dropped: an unexplained
    # tool call is a finding, and losing spans is the failure this module exists
    # to avoid.
    orphans = [_tool_call(s) for spans in by_call_id.values() for s in spans]
    if orphans and turns:
        turns[-1].setdefault("tool_results", []).extend(orphans)
    return turns


def condense(tree: Optional[dict]) -> Optional[dict]:
    """The fetched tree as a readable run: a header, then the turns.

    Faithful about the conversation, silent about the plumbing. See the note
    above for what goes and why.
    """
    if not tree:
        return None

    spans = list(_walk(tree))

    # The system prompt and the task are constant for the run and large, so they
    # are lifted out of the first turn's history and written once. This has to
    # happen before the turns are built, because each turn's `input` stands the
    # system prompt in rather than repeating it.
    first = next((s for s in spans if s.get("run_type") == "llm"), None)
    system, task = None, None
    for message in _history(first or {}):
        kw = _kwargs(message)
        if kw.get("type") == "system" and system is None:
            system = _text(kw.get("content"))
        elif kw.get("type") == "human" and task is None:
            task = _text(kw.get("content"))

    turns = _turns(tree, system)

    # Tool schemas are repeated on every model span and run to hundreds of KB.
    # The names are the part a reader needs; the schemas are in the code.
    available: list = []
    for span in spans:
        params = (span.get("extra") or {}).get("invocation_params") or {}
        for tool in params.get("tools") or []:
            name = ((tool.get("function") or {}).get("name")
                    if isinstance(tool, dict) else None)
            if name and name not in available:
                available.append(name)

    # Turns per member, not calls per member: a turn the pool retried was still
    # one turn, and its failed attempts are counted as failures below.
    models: dict = {}
    for turn in turns:
        name = turn.get("model")
        if name:
            models[name] = models.get(name, 0) + 1

    failed = sum(1 for turn in turns for attempt in turn.get("attempts") or []
                 if attempt.get("status") == "error")

    # The root span is often still `pending` when the fetch happens -- it closes
    # last and the tracer flushes asynchronously -- so its own end time and
    # latency are usually absent. The last span to finish is the honest answer.
    ends = [s["end_time"] for s in spans if s.get("end_time")]
    end = tree.get("end_time") or (max(ends) if ends else None)
    seconds = _seconds(tree)
    if seconds is None and end and tree.get("start_time"):
        seconds = round((_moment(end) -
                         _moment(tree["start_time"])).total_seconds(), 3)

    return {
        "run": {
            "status": tree.get("status"),
            "start": tree.get("start_time"),
            "end": end,
            "seconds": seconds,
            "turns": len(turns),
            "tool_calls": sum(len(t.get("tool_results") or []) for t in turns),
            "tokens": _tokens(tree),
            # LangSmith's list price for these models. Every account here is on
            # a free tier, so this is what the run would have cost, not what it
            # did -- kept as a size, not a bill.
            "notional_cost_usd": tree.get("total_cost"),
            "provider_failures": failed,
            "models": models,
            # Where the untouched tree still lives, until it expires.
            "langsmith_url": (_LANGSMITH_APP + tree["app_path"]
                              if tree.get("app_path") else None),
            "spans_fetched": len(spans),
        },
        "task": task,
        "tools_available": available,
        "system_prompt": system,
        "turns": turns,
    }


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

    `Client.traces` is async-only, and this is called from an agent's `run`, which
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
    """Write the condensed run plus what the fetch itself needs to be read.

    The fetched tree is condensed on the way to disk rather than stored and
    condensed later: nothing `condense` drops is anything a reader of this file
    was going to use, and keeping both copies would mean the 22 MB one is what
    gets opened by accident.

    `meta` is recorded even when the tree is None, so a run with no trace says
    so on disk instead of leaving an absent file to be explained later.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta, **(condense(tree) or {"run": None})}
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def locate(runs) -> tuple[Optional[str], Optional[str]]:
    """What the fetch needs to name this trace: `(trace_id, project_id)`.

    Reading any run's `trace_id` does not work here, and neither does taking
    `runs[0]`. `RunCollectorCallbackHandler` persists a run only when it has no
    parent, so what arrives is not one tree but *several detached roots* -- a
    session with two model turns and one tool call yields six, one per
    LangGraph turn plus one for each `RouterChatModel`/provider pair -- and each
    of them carries its own id as its `trace_id`. Asking LangSmith for a leaf's
    id returns an empty trace, and the v2 endpoint reports that as 200 with no
    runs: a trace recorded as `null` with nothing to say why.

    The session's own root is the one that started first; everything else
    begins inside it. `start_time` says so directly, and the collector's own
    order does not, because it appends in *completion* order and so puts the
    innermost LLM call first and the root last.

    `project_id` is the same field LangSmith calls `session_id`. The collector
    leaves it unset, so this is normally None and `resolve_project_id` looks
    the project up by name instead.
    """
    if not runs:
        return None, None
    root = min(runs, key=lambda run: run.start_time)
    trace_id = getattr(root, "trace_id", None) or root.id
    project_id = getattr(root, "session_id", None)
    return str(trace_id), str(project_id) if project_id else None


def about(workdir, harness: str, floor: int, members: int, **extra) -> dict:
    """The `meta` every agent records, plus whatever that agent adds.

    Four keys were rebuilt by hand in each `run()`. What differs is the tail --
    the coding agent's `peers`, the explorer's `research_dir` and
    `search_accounts` -- and that stays at the call site where it means
    something.
    """
    return {"workdir": str(workdir), "harness": harness,
            "context_floor": floor, "eligible_providers": members, **extra}


def record(runs, path: Optional[Path], meta: dict) -> Optional[Path]:
    """Fetch the run tree and write it to `path`; nothing when `path` is None.

    `runs` is what `collect_runs()` gathered around the run. The record names
    the trace and the project the fetch actually queried, then the caller's
    `meta`. Never raises: this runs after the work is done.
    """
    if path is None:
        return None
    trace_id, project_id = locate(runs)
    project_id = resolve_project_id(project_id)
    tree = fetch_tree(trace_id, project_id) if trace_id else None
    written = write(path, tree, meta={"trace_id": trace_id,
                                      "project_id": project_id, **meta,
                                      "tracing_enabled": tracing_enabled()})
    if written:
        logger.info(f"Wrote the run record to {written}")
    return written
