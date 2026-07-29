"""A stub agent, for exercising the runner without spending free-tier quota.

It emits the same EVAL_TRACE_FILE JSONL the real agent writes, so the metrics
and taxonomy paths are exercised for real rather than mocked. FAKE_MODE picks
which outcome to simulate:

    fix       edits the target correctly              -> pass
    noop      reads the file, never edits it          -> fail / stopping
    badedit   reads the file, edits fail to apply     -> fail / tooling
    lost      never opens the target file             -> fail / retrieval
    tamper    rewrites the immutable test to pass     -> tampered
"""

import json
import os
import sys
import time
from pathlib import Path

FIXED = '''"""Reading the Retry-After header a provider sends with a rate-limit response.

The router uses this to decide how long to keep an account in cooldown before
trying it again.
"""


def parse_retry_after(headers):
    """Seconds to wait before retrying, or None if the header is absent."""
    for name, value in headers.items():
        # HTTP header names are case-insensitive; providers send "Retry-After".
        if name.lower() == "retry-after":
            return int(value)
    return None
'''


def emit(trace, event, **fields):
    if not trace:
        return
    record = {"ts": round(time.time(), 3), "event": event, **fields}
    with open(trace, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def main():
    workdir = Path(sys.argv[1])
    sys.stdin.read()
    trace = os.environ.get("EVAL_TRACE_FILE")
    mode = os.environ.get("FAKE_MODE", "fix")

    # One agent step: the router call, a rate-limited attempt, then a reroute
    # that succeeds -- the shape every real step has.
    emit(trace, "llm_start", run_id="s1", model="RouterChatModel")
    emit(trace, "llm_start", run_id="p1", parent_run_id="s1", model="llama-3.3-70b")
    emit(trace, "llm_error", run_id="p1", ok=False, detail="rate_limit_exceeded (429)")
    emit(trace, "llm_start", run_id="p2", parent_run_id="s1", model="gemini-3.5-flash")
    emit(trace, "llm_end", run_id="p2", ok=True, tokens_in=912, tokens_out=140)

    target = workdir / "retry.py"

    if mode != "lost":
        emit(trace, "tool_start", run_id="t1", tool="read_file",
             args=json.dumps({"path": "retry.py"}))
        emit(trace, "tool_end", run_id="t1", tool="read_file", ok=True,
             output=target.read_text(encoding="utf-8"))
    else:
        emit(trace, "tool_start", run_id="t1", tool="ls", args=json.dumps({"path": "."}))
        emit(trace, "tool_end", run_id="t1", tool="ls", ok=True, output="docs/")

    if mode == "fix":
        emit(trace, "tool_start", run_id="t2", tool="edit_file",
             args=json.dumps({"path": "retry.py"}))
        target.write_text(FIXED, encoding="utf-8")
        emit(trace, "tool_end", run_id="t2", tool="edit_file", ok=True, output="ok")
        emit(trace, "tool_start", run_id="t3", tool="execute",
             args=json.dumps({"command": "python -m pytest tests"}))
        emit(trace, "tool_end", run_id="t3", tool="execute", ok=True, output="1 passed")

    elif mode == "badedit":
        for index in (2, 3):
            emit(trace, "tool_start", run_id=f"t{index}", tool="edit_file",
                 args=json.dumps({"path": "retry.py"}))
            emit(trace, "tool_end", run_id=f"t{index}", tool="edit_file", ok=True,
                 output="Error: string to replace not found in file")

    elif mode == "tamper":
        (workdir / "tests" / "test_retry.py").write_text(
            "def test_matches_header_case_insensitively():\n    assert True\n",
            encoding="utf-8")
        emit(trace, "tool_start", run_id="t2", tool="edit_file",
             args=json.dumps({"path": "tests/test_retry.py"}))
        emit(trace, "tool_end", run_id="t2", tool="edit_file", ok=True, output="ok")

    print(f"[fake agent] mode={mode} done")


if __name__ == "__main__":
    main()
