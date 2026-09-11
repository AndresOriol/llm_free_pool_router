"""Reading a recorded run, without a pool and without a real run.

Two things can be wrong here silently, and both have already happened once in
this repository's history of reading its own traces.

The first is **an absence that reads as a fact**. A trace file that is present
but unreadable must not be reported as "this run was never traced", and a
`where` clause naming a field a record does not have must not match. Both would
produce a confident finding about a run nobody actually read.

The second is **a signature that matches more than it names**. It is what closes
and reopens an issue months later, so an empty one matching everything, or a
numeric comparison silently falling back to string ordering, would quietly
retire a real failure.
"""

import json
from pathlib import Path

from agent.improve import records


def _eval_run(root: Path, name: str, verdict: dict, events=(), stderr="") -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "run.json").write_text(json.dumps(verdict), encoding="utf-8")
    if events:
        (directory / "trace.jsonl").write_text(
            "\n".join(json.dumps(e) for e in events), encoding="utf-8")
    if stderr:
        (directory / "stderr.log").write_text(stderr, encoding="utf-8")
    return directory


def _live_run(root: Path, name: str, tree: str) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "trace.json").write_text(tree, encoding="utf-8")
    return directory


def test_discovers_both_kinds_and_dates_them_from_the_name(tmp_path):
    runs = tmp_path / "evals" / "results" / "runs"
    _eval_run(runs, "20260907T074310Z_worst-first_code_r1", {"outcome": "pass"})
    _live_run(tmp_path / "live", "20260901T101010Z_task", '{"turns": []}')

    found = records.discover([runs, tmp_path / "live"])

    assert [r.kind for r in found] == ["eval", "live"], "newest first"
    assert found[0].ts.startswith("2026-09-07T07:43:10")
    assert found[1].verdict == {}, "a live run carries no verdict at all"


def test_a_missing_root_is_not_an_error(tmp_path):
    assert records.discover([tmp_path / "nothing-here"]) == []


def test_an_unparseable_tree_is_not_a_missing_tree(tmp_path):
    """The distinction the whole module turns on: "would not parse" sends a
    reader to the file, "was never written" sends them to the tracing config."""
    directory = _live_run(tmp_path, "run", '{"turns": [ truncated')
    record = records.discover([tmp_path])[0]

    assert records.tree(record) == {}
    assert record.has_tree, "the file is there; it just did not parse"
    assert directory.joinpath("trace.json").is_file()


def test_events_survive_a_truncated_last_line(tmp_path):
    runs = tmp_path / "runs"
    _eval_run(runs, "r1", {"outcome": "fail"},
              events=[{"event": "llm_start", "ts": 1.0},
                      {"event": "llm_end", "ts": 2.0}])
    path = runs / "r1" / "trace.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + '\n{"event": "tool_st',
                    encoding="utf-8")

    assert len(records.events(records.discover([runs])[0])) == 2


def test_routing_gaps_name_the_longest_silence(tmp_path):
    """The instrument that separates "the agent looped" from "one call hung"."""
    runs = tmp_path / "runs"
    _eval_run(runs, "r1", {"outcome": "fail"},
              events=[{"event": "llm_start", "ts": 0.0},
                      {"event": "llm_start", "ts": 2.0},
                      {"event": "llm_start", "ts": 902.0}])

    gaps = records.routing_gaps(records.discover([runs])[0])

    assert gaps[0].startswith("900.0s"), gaps
    assert "call 3" in gaps[0]


def test_grep_streams_every_trace_file_and_names_which(tmp_path):
    runs = tmp_path / "runs"
    _eval_run(runs, "r1", {"outcome": "fail"},
              events=[{"event": "tool_error", "detail": "GraphRecursionError"}],
              stderr="Routing to gemini_1\nGraphRecursionError again\n")

    hits = records.grep(records.discover([runs])[0], "GraphRecursionError")

    assert len(hits) == 2
    assert {h.split(":")[0] for h in hits} == {"trace.jsonl", "stderr.log"}


class TestSignatures:
    def _record(self, tmp_path, verdict, stderr=""):
        runs = tmp_path / "runs"
        _eval_run(runs, "r1", verdict, stderr=stderr)
        return records.discover([runs])[0]

    def test_an_empty_signature_matches_nothing(self, tmp_path):
        """It would otherwise read as "this failure is everywhere"."""
        record = self._record(tmp_path, {"outcome": "fail"})
        assert not records.matches(record, {})
        assert not records.matches(record, {"where": {}})

    def test_numeric_comparisons_compare_numbers(self, tmp_path):
        record = self._record(tmp_path, {"tokens_in": 550384})
        assert records.matches(record, {"where": {"tokens_in": "> 200000"}})
        assert not records.matches(record, {"where": {"tokens_in": "> 900000"}})
        # String ordering would call "550384" < "9" true and close the issue.
        assert records.matches(record, {"where": {"tokens_in": "< 900000"}})

    def test_a_clause_on_a_field_the_record_lacks_never_matches(self, tmp_path):
        """What stops a signature about eval verdicts matching a live run."""
        record = self._record(tmp_path, {"outcome": "fail"})
        assert not records.matches(record, {"where": {"failure_class": "stopping"}})

    def test_a_list_field_matches_on_membership(self, tmp_path):
        record = self._record(tmp_path, {"models_used": ["gemma-3", "gemini-3"]})
        assert records.matches(record, {"where": {"models_used": "gemma-3"}})
        assert not records.matches(record, {"where": {"models_used": "groq"}})

    def test_grep_and_where_are_both_required(self, tmp_path):
        record = self._record(tmp_path, {"outcome": "fail"},
                              stderr="waiting 41s for the next account\n")
        signature = {"where": {"outcome": "fail"}, "grep": "waiting \\d+s"}
        assert records.matches(record, signature)
        assert not records.matches(record, {**signature,
                                            "where": {"outcome": "pass"}})
        assert not records.matches(record, {**signature, "grep": "never-said"})

    def test_kind_alone_is_a_signature(self, tmp_path):
        record = self._record(tmp_path, {"outcome": "fail"})
        assert records.matches(record, {"kind": "eval"})
        assert not records.matches(record, {"kind": "live"})
