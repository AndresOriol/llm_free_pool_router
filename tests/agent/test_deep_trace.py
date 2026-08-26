"""Checks for the deepagents arm's record -- the LangSmith run tree.

The fetch itself needs the network, so what is checked here is the part that
does not: rebuilding the nesting from the flat, `start_time`-ordered list the
v2 `traces.list_runs` returns. That reassembly is the whole reason the migration
off `read_run(load_child_runs=True)` is more than a rename, so it is also the
part worth a test.

No framework: `python -m tests.agent.test_deep_trace` (or run the file).
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.deep.trace import nest, write


class _Run:
    """Stand-in for a v2 `Run`: a pydantic model, so it knows how to dump."""

    def __init__(self, run_id, trace_id, ancestors, name):
        self._data = {"id": run_id, "trace_id": trace_id, "name": name,
                      "parent_run_ids": list(ancestors)}

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

    # Children come back in the order they arrived, which is start_time order.
    # The response is ordered; the dict that rebuilds the tree must not undo it.
    wide = [_Run("root", "root", [], "agent")] + [
        _Run(f"k{i}", "root", ["root"], f"step-{i}") for i in range(10)
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
        assert payload["trace"] is None
        assert payload["meta"]["trace_id"] == "root"

        write(target, tree, {"trace_id": "root", "project_id": "p"})
        assert json.loads(target.read_text(encoding="utf-8"))["trace"]["id"] == "root"

    print("deep trace: all checks passed")


def test_deep_trace():
    """Collected by pytest -- see the note in test_restricted_backend.py."""
    _run()


if __name__ == "__main__":
    _run()
