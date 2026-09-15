import pytest
from pathlib import Path
from evals import splits

def test_load():
    data = splits.load()
    assert "train" in data
    assert "holdout" in data
    assert "http-headers" in data["train"]
    assert "bots" in data["holdout"]

def test_split_of():
    assert splits.split_of("retry-after-case") == "train"
    assert splits.split_of("stock-export") == "holdout"
    assert splits.split_of("http-headers") == "train"
    assert splits.split_of("bots") == "holdout"
    assert splits.split_of("scenario/http-headers/retry-after-case") == "train"
    assert splits.split_of("scenario/export/stock-export") == "holdout"

    with pytest.raises(ValueError):
        splits.split_of("nonexistent-scenario-xyz")

def test_score():
    records = [
        {"scenario": "retry-after-case", "verified": True},
        {"scenario": "threshold-off-by-one", "verified": False},
        {"scenario": "stock-export", "verified": True},
        {"scenario": "duration-notes", "verified": False},
        {"scenario": "unknown-scenario", "verified": True},
    ]
    res = splits.score(records)
    assert res["train"] == {"solved": 1, "runs": 2}
    assert res["holdout"] == {"solved": 1, "runs": 2}
    assert res["combined"] == {"solved": 2, "runs": 4}
