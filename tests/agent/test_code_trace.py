"""Checks for the coding agent's record -- the LangSmith run tree.

The fetch itself needs the network, so what is checked here is everything that
does not: rebuilding the nesting from the flat list v2 `traces.list_runs`
returns, and condensing that tree into the record that actually goes to disk.

Both are places where a wrong answer looks right. A tree nested in arrival
order reads as a plausible run until you notice it is backwards, and a condense
that drops the wrong span leaves a record that is merely incomplete rather than
obviously broken -- so the assertions here are mostly counts.

No framework: `python -m tests.agent.test_code_trace` (or run the file).
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.runtime.run_tree import condense, nest, write


class _Run:
    """Stand-in for a v2 `Run`: a pydantic model, so it knows how to dump."""

    def __init__(self, run_id, trace_id, ancestors, name, start=None, **extra):
        self._data = {"id": run_id, "trace_id": trace_id, "name": name,
                      "parent_run_ids": list(ancestors)}
        if start is not None:
            self._data["start_time"] = start
        self._data.update(extra)

    def model_dump(self, **_kwargs):
        return dict(self._data)


def _names(node):
    """Child names at one level, so ordering is checkable."""
    return [child["name"] for child in node.get("child_runs", [])]


def _run():
    # Nothing to nest is not an empty tree, it is no tree -- the caller writes
    # `"trace": null` and says so, rather than recording a plausible-looking
    # object with no spans in it.
    assert nest([]) is None

    # The ordinary case: a root, two children, one grandchild. `parent_run_ids`
    # is the chain root-first, so the immediate parent is its last entry --
    # reading the *first* entry instead would flatten every level onto the root
    # and still produce a tree that looked fine.
    runs = [
        _Run("root", "root", [], "agent"),
        _Run("a", "root", ["root"], "llm-1"),
        _Run("b", "root", ["root"], "tool-1"),
        _Run("c", "root", ["root", "b"], "tool-1-inner"),
    ]
    tree = nest(runs)
    assert tree["id"] == "root", tree["id"]
    assert _names(tree) == ["llm-1", "tool-1"], _names(tree)
    assert _names(tree["child_runs"][1]) == ["tool-1-inner"]
    assert "child_runs" not in tree["child_runs"][0]

    # Every span survives the round trip. The count is the check that caught
    # the one-span tree that used to pass for a working trace.
    def _count(node):
        return 1 + sum(_count(kid) for kid in node.get("child_runs", []))
    assert _count(tree) == 4, _count(tree)

    # Children come back in start_time order however the batch arrived. This is
    # the check for the ordering bug: `traces.list_runs` returns newest-first,
    # and appending in arrival order built every tree backwards -- last turn
    # first -- which reads as a plausible run until you notice the context
    # shrinking. So the batch here is handed over reversed on purpose.
    wide = [_Run("root", "root", [], "agent", "2026-01-01T00:00:00Z")] + [
        _Run(f"k{i}", "root", ["root"], f"step-{i}", f"2026-01-01T00:00:{i:02d}Z")
        for i in reversed(range(10))
    ]
    assert _names(nest(wide)) == [f"step-{i}" for i in range(10)]

    # A span whose parent is missing from the batch is kept, not dropped. It
    # hangs off the run the trace is named after, so the count still holds even
    # though the shape is wrong -- silently losing it is the worse failure.
    orphaned = [
        _Run("root", "root", [], "agent"),
        _Run("lost", "root", ["root", "never-sent"], "tool-2"),
    ]
    salvaged = nest(orphaned)
    assert salvaged["id"] == "root", salvaged["id"]
    assert _count(salvaged) == 2, salvaged

    # ... and when the trace's own root is the one missing, the batch still
    # nests under whatever is left rather than returning None.
    headless = nest([_Run("x", "root", ["root"], "orphan-1"),
                     _Run("y", "root", ["root"], "orphan-2")])
    assert headless is not None and _count(headless) == 2, headless

    # A run is not its own parent, however the vendor spells it.
    loop = nest([_Run("self", "self", ["self"], "cycle")])
    assert loop is not None and _count(loop) == 1, loop

    # The meta block lands even with no tree, so a run with no trace explains
    # itself on disk instead of leaving an absent file to be explained later.
    with tempfile.TemporaryDirectory() as d:
        target = Path(d) / "nested" / "run-tree.json"
        assert write(target, None, {"trace_id": "root", "project_id": None})
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["run"] is None
        assert payload["meta"]["trace_id"] == "root"

        write(target, tree, {"trace_id": "root", "project_id": "p"})
        written = json.loads(target.read_text(encoding="utf-8"))
        assert written["run"]["spans_fetched"] == 4, written["run"]

    print("code trace: all checks passed")


def _msg(kind, text, tool_calls=None, name=None):
    """A serialised LangChain message, in the wrapped shape a span carries."""
    kwargs = {"type": kind, "content": text}
    if tool_calls:
        kwargs["tool_calls"] = tool_calls
    if name:
        kwargs["name"] = name
    return {"lc": 1, "type": "constructor", "kwargs": kwargs}


def _llm(run_id, parent, start, history, text="", tool_calls=None, **extra):
    """One provider call, with the history it saw and the message it produced."""
    message = {"lc": 1, "type": "constructor",
               "kwargs": {"type": "ai", "content": text,
                          "tool_calls": tool_calls or [],
                          "response_metadata": {"model_name": "gemini-3.6-flash",
                                                "model_provider": "google_genai",
                                                "finish_reason": "STOP"}}}
    return _Run(run_id, "root", ["root", parent], extra.pop("name", "RouterChatModel"),
                start, run_type="llm", status=extra.pop("status", "success"),
                end_time=extra.pop("end_time", start),
                inputs={"messages": [history]},
                outputs={"generations": [[{"message": message}]]},
                prompt_tokens=100, completion_tokens=10, total_tokens=110,
                **extra)


def _tool(run_id, parent, start, name, args, call_id, output):
    return _Run(run_id, "root", ["root", parent], name, start, run_type="tool",
                status="success", end_time=start, inputs=args,
                outputs={"output": {"content": output, "status": "success",
                                    "tool_call_id": call_id}})


def _condensed():
    """A two-turn run: the model calls a tool, sees the result, then answers."""
    system = _msg("system", "SYSTEM PROMPT")
    task = _msg("human", "do the thing")
    call = {"id": "call_1", "name": "read_file", "args": {"file_path": "/a.md"}}
    turn_1 = [system, task]
    turn_2 = turn_1 + [_msg("ai", "", [call]), _msg("tool", "file body",
                                                    name="read_file")]
    runs = [
        _Run("root", "root", [], "LangGraph", "2026-01-01T00:00:00Z",
             run_type="chain", status="pending", total_tokens=220,
             app_path="/o/1/trace/root"),
        # The middleware wrapper. It carries a copy of the history too; the
        # point of the condense is that no copy of it reaches disk.
        _Run("mw1", "root", ["root"], "FilesystemMiddleware.wrap_model_call",
             "2026-01-01T00:00:01Z", run_type="chain",
             inputs={"messages": [turn_1]}),
        _llm("llm1", "mw1", "2026-01-01T00:00:02Z", turn_1, tool_calls=[call]),
        _tool("t1", "root", "2026-01-01T00:00:03Z", "read_file",
              {"file_path": "/a.md"}, "call_1", "file body"),
        _Run("mw2", "root", ["root"], "FilesystemMiddleware.wrap_model_call",
             "2026-01-01T00:00:04Z", run_type="chain",
             inputs={"messages": [turn_2]}),
        _llm("llm2", "mw2", "2026-01-01T00:00:05Z", turn_2, text="done"),
    ]
    return condense(nest(runs))


def _run_condense():
    out = _condensed()

    # The two shapes of noise are gone: no chain span survives as a turn, and
    # the middleware's duplicate copy of the history is nowhere on disk.
    assert out["run"]["turns"] == 2, out["run"]
    assert out["run"]["tool_calls"] == 1, out["run"]
    assert out["run"]["spans_fetched"] == 6, out["run"]
    assert "FilesystemMiddleware" not in json.dumps(out)

    # The system prompt is written once and stood in for inside each turn, so
    # its 15 KB does not land seventeen times over.
    assert out["system_prompt"] == "SYSTEM PROMPT"
    assert out["task"] == "do the thing"
    assert json.dumps(out["turns"]).count("SYSTEM PROMPT") == 0

    first, second = out["turns"]
    assert first["n"] == 1 and second["n"] == 2

    # Every call keeps the whole history it was handed, in order, so what
    # entered the model at each point is readable without reconstructing it.
    assert [m["role"] for m in first["input"]] == ["system", "human"], first
    assert first["input"][0]["text"].startswith("<the system_prompt")
    assert first["input"][1]["text"] == "do the thing"
    assert [m["role"] for m in second["input"]] ==         ["system", "human", "ai", "tool"], second
    assert second["input"][3]["text"] == "file body", second["input"]
    assert [t["context_messages"] for t in out["turns"]] == [2, 4], out["turns"]
    assert not any(t.get("context_rewritten") for t in out["turns"])

    # ... and what came back out. A turn that only called tools has no text, so
    # it is omitted rather than written as "".
    assert "text" not in first["output"], first["output"]
    assert first["output"]["tool_calls"][0]["name"] == "read_file", first
    assert first["output"]["tool_calls"][0]["args"] == {"file_path": "/a.md"}
    assert second["output"]["text"] == "done", second

    # The tool is grouped under the turn that asked for it, linked by id, and
    # its arguments are not repeated from `output.tool_calls` above.
    result = first["tool_results"][0]
    assert result["tool_call_id"] == "call_1", result
    assert result["name"] == "read_file", result
    assert result["output"] == "file body", result
    assert "args" not in result, result
    assert "tool_results" not in second, second

    # The root closes last and is usually still `pending` when the fetch runs,
    # so the wall time comes from the last span to finish rather than being lost.
    assert out["run"]["seconds"] == 5.0, out["run"]

    # A lone successful attempt only restates the turn, so it is not recorded.
    assert not any("attempts" in t for t in out["turns"]), out["turns"]

    print("code trace condense: all checks passed")


def _run_failover_and_compaction():
    """The two cases the condense spends bytes on, because nothing else holds
    them: the pool retrying, and summarization rewriting the history."""
    system = _msg("system", "SYSTEM PROMPT")
    task = _msg("human", "do the thing")
    long_history = [system, task] + [_msg("ai", f"step {i}") for i in range(6)]
    # Summarization replaces the middle with a summary, so this history is no
    # longer the previous one extended.
    compacted = [system, task, _msg("ai", "SUMMARY OF EARLIER WORK")]
    runs = [
        _Run("root", "root", [], "LangGraph", "2026-01-01T00:00:00Z",
             run_type="chain", status="success",
             end_time="2026-01-01T00:00:09Z"),
        _Run("mw1", "root", ["root"], "wrap_model_call", "2026-01-01T00:00:01Z",
             run_type="chain"),
        _llm("llm1", "mw1", "2026-01-01T00:00:02Z", long_history, text="a"),
        _Run("mw2", "root", ["root"], "wrap_model_call", "2026-01-01T00:00:04Z",
             run_type="chain"),
        # The pool's first pick 429s and the second answers. Both attempts are
        # children of the same wrapper, which is what makes them one turn.
        _llm("bad", "mw2", "2026-01-01T00:00:05Z", compacted, status="error",
             name="ChatGoogleGenerativeAI", error="429 RESOURCE_EXHAUSTED"),
        _llm("llm2", "mw2", "2026-01-01T00:00:06Z", compacted, text="b"),
    ]
    out = condense(nest(runs))

    assert out["run"]["turns"] == 2, out["run"]
    first, second = out["turns"]

    # The retry is recorded, and counted, because the failover is the thing this
    # project does and nothing else in the record shows it.
    assert "attempts" not in first, first
    assert len(second["attempts"]) == 1, second["attempts"]
    assert second["attempts"][0]["status"] == "error", second["attempts"]
    assert "429" in second["attempts"][0]["error"], second["attempts"]
    assert out["run"]["provider_failures"] == 1, out["run"]

    # A retried turn is still one turn, so the member is counted once.
    assert out["run"]["models"] == {"gemini-3.6-flash": 2}, out["run"]["models"]

    # The context shrank instead of growing, which is summarization seen from
    # outside. `input` holds what the model was actually handed either way; the
    # flag is what says this turn is where to look.
    assert [t["context_messages"] for t in out["turns"]] == [8, 3], out["turns"]
    assert not first.get("context_rewritten"), first
    assert second["context_rewritten"] is True, second
    assert [m["role"] for m in second["input"]] == ["system", "human", "ai"]
    assert second["input"][2]["text"] == "SUMMARY OF EARLIER WORK", second
    # The six steps the summary replaced are in the turn before it, and only
    # there -- which is the point of keeping both histories.
    assert "step 4" in json.dumps(first["input"]), first
    assert "step 4" not in json.dumps(second["input"]), second

    # Nothing to condense is not an empty run, it is no run.
    assert condense(None) is None

    print("code trace failover/compaction: all checks passed")

def test_code_trace():
    """Collected by pytest -- see the note in test_restricted_backend.py."""
    _run()


def test_code_trace_condense():
    _run_condense()


def test_code_trace_failover_and_compaction():
    _run_failover_and_compaction()


if __name__ == "__main__":
    _run()
    _run_condense()
    _run_failover_and_compaction()


def test_a_refused_call_contributes_no_tokens_and_one_bounce():
    """A bounce is a quota cost, not a token cost.

    The provider refuses a 429 or a 404 at the gate, so `llm_error` carries no
    token count -- checked across 40 runs, 247 bounces, none of them with one.
    Reporting bounces in the same breath as `tokens_in` invited exactly the
    wrong reading (docs/06-agent.md#611).
    """
    from evals import metrics

    events = [
        {"event": "llm_start", "run_id": "s1", "model": "RouterChatModel"},
        {"event": "llm_start", "run_id": "p1", "model": "gemini-2.5-flash"},
        {"event": "llm_error", "run_id": "p1", "ok": False, "detail": "404 NOT_FOUND"},
        {"event": "llm_start", "run_id": "p2", "model": "gemini-3.5-flash"},
        {"event": "llm_end", "run_id": "p2", "ok": True, "tokens_in": 30_000,
         "tokens_out": 200},
    ]
    measured = metrics.from_trace(events)

    assert measured["failover_bounces"] == 1
    assert measured["tokens_in"] == 30_000
    assert measured["provider_calls"] == 2
    assert measured["tokens_per_call"] == 15_000
    assert measured["bounces_per_call"] == 0.5
