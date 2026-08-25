"""Checks for the JSONL eval trace -- the file every automatic metric in
docs/10-metrics.md is derived from, so its shape is a contract.

No framework: `python -m tests.agent.test_trace` (or run the file).
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.runtime.trace import JsonlTracer, tracer_from_env


class _Generation:
    """Stand-in for a ChatGeneration: a .message carrying .usage_metadata."""

    def __init__(self, usage, text=""):
        self.message = type("M", (), {"usage_metadata": usage})()
        self.text = text


class _Response:
    """Stand-in for an LLMResult."""

    def __init__(self, usage=None, llm_output=None, text=""):
        self.generations = [[_Generation(usage, text)]] if usage is not None else [[]]
        self.llm_output = llm_output


def _lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _run():
    with tempfile.TemporaryDirectory() as d:
        # Tracing is off unless the env var is set.
        os.environ.pop("EVAL_TRACE_FILE", None)
        assert tracer_from_env() is None

        # ... and on when it is, including creating a missing parent dir.
        target = Path(d) / "nested" / "trace.jsonl"
        os.environ["EVAL_TRACE_FILE"] = str(target)
        try:
            tracer = tracer_from_env()
        finally:
            os.environ.pop("EVAL_TRACE_FILE", None)
        assert tracer is not None and target.parent.is_dir()

        # A provider call: model name comes from metadata, tokens from usage.
        tracer.on_chat_model_start({"name": "ChatOpenAI"}, [],
                                   run_id="r1", metadata={"ls_model_name": "llama-3.3-70b"})
        tracer.on_llm_end(_Response(usage={"input_tokens": 11, "output_tokens": 7}),
                          run_id="r1")
        start, end = _lines(target)
        assert start == {"ts": start["ts"], "event": "llm_start", "run_id": "r1",
                         "model": "llama-3.3-70b"}, start
        assert (end["tokens_in"], end["tokens_out"], end["ok"]) == (11, 7, True), end

        # What the model actually said is kept, clipped like everything else.
        # Without it a `<think>` block that poisoned a finding is invisible:
        # once envelope.py has parsed the reply, a model that wrote nonsense
        # and a parser that mangled sense look identical.
        tracer.on_chat_model_start({"name": "ChatOpenAI"}, [], run_id="rt")
        tracer.on_llm_end(_Response(usage={"input_tokens": 1, "output_tokens": 2},
                                    text="<think>hmm</think>STATUS: DONE"),
                          run_id="rt")
        said = _lines(target)[-1]
        assert "<think>" in said["text"], said
        tracer.on_llm_end(_Response(usage={"input_tokens": 1}, text="z" * 50_000),
                          run_id="rt")
        assert "50000 chars" in _lines(target)[-1]["text"]

        # A rerouted attempt is an llm_error -- this is the failover-bounce count.
        tracer.on_llm_error(RuntimeError("rate_limit_exceeded"), run_id="r2")
        bounce = _lines(target)[-1]
        assert bounce["event"] == "llm_error" and bounce["ok"] is False
        assert "rate_limit_exceeded" in bounce["detail"]

        # Tool events name their tool on both ends, so counting failed
        # edit_file calls (the `tooling` failure class) needs no join.
        tracer.on_tool_start({"name": "edit_file"}, '{"path": "a.py"}', run_id="t1")
        tracer.on_tool_error(ValueError("string not found"), run_id="t1")
        opened, failed = _lines(target)[-2:]
        assert opened["tool"] == "edit_file" and "a.py" in opened["args"]
        assert failed["tool"] == "edit_file" and failed["ok"] is False

        # Whole files pass through tool results; they must be clipped, and the
        # clip must say how much was dropped.
        tracer.on_tool_start({"name": "read_file"}, "x", run_id="t2")
        tracer.on_tool_end("y" * 50_000, run_id="t2")
        clipped = _lines(target)[-1]
        assert len(clipped["output"]) < 2200, len(clipped["output"])
        assert "50000 chars" in clipped["output"], clipped["output"]

        # Fallback path: no usage_metadata, only an OpenAI-style llm_output.
        tracer.on_llm_end(_Response(llm_output={"token_usage": {"prompt_tokens": 3,
                                                                "completion_tokens": 4}}),
                          run_id="r3")
        assert (_lines(target)[-1]["tokens_in"], _lines(target)[-1]["tokens_out"]) == (3, 4)

        # Missing usage entirely: the record still lands, just without tokens.
        tracer.on_llm_end(_Response(), run_id="r4")
        assert "tokens_in" not in _lines(target)[-1]

        # Unknown model shape degrades to a marker, never raises.
        tracer.on_chat_model_start(None, [], run_id="r5")
        assert _lines(target)[-1]["model"] == "unknown"

    print("trace: all checks passed")


def test_trace():
    """Collected by pytest -- see the note in test_restricted_backend.py. The
    trace's event shape is a contract (docs/07-observability.md#74), and a
    contract nothing runs is not one."""
    _run()


if __name__ == "__main__":
    _run()
