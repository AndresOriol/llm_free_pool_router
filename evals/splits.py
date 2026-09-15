"""Train and holdout splits for evaluation scenarios.

The split is data rather than a function because it is part of the method
(documented in /docs/08-evaluation-method.md §8.11), and it lives in this repo
rather than agent_evals so the gate can be computed from recorded run.json files
even when the scenario repo is not on disk at all.

It is keyed by topic because a topic branch in agent_evals is one codebase;
splitting a topic across the two sides would put the holdout's codebase in train.
An undeclared scenario is an error rather than a default.
"""

from pathlib import Path
import yaml

REPO = Path(__file__).resolve().parents[1]
SPLITS_FILE = REPO / "evals" / "splits.yaml"


def load(path: Path = None) -> dict:
    p = Path(path) if path else SPLITS_FILE
    if not p.is_file():
        raise RuntimeError(f"Splits file not found at {p}")
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _maps():
    data = load()
    s2s, t2s = {}, {}
    for sp in ("train", "holdout"):
        for topic, info in (data.get(sp, {}) or {}).items():
            t2s[topic] = sp
            if isinstance(info, dict) and "scenarios" in info:
                for s in info["scenarios"]:
                    s2s[s] = sp
            s2s[topic] = sp
    return s2s, t2s


def split_of(scenario_or_topic: str) -> str:
    """Return 'train' or 'holdout', or raise ValueError if unlisted."""
    s2s, t2s = _maps()
    val = scenario_or_topic
    if val.startswith("scenario/"):
        parts = val.split("/")
        val = parts[2] if len(parts) >= 3 else (parts[1] if len(parts) >= 2 else val)
    elif "/" in val:
        parts = val.split("/")
        val = parts[1] if len(parts) >= 2 else val

    if val in s2s:
        return s2s[val]
    if val in t2s:
        return t2s[val]
    raise ValueError(f"Scenario or topic {scenario_or_topic!r} is not listed in any split in /evals/splits.yaml")


def score(records: list) -> dict:
    """Compute solved count and runs per split over run records.

    Returns dict with 'train', 'holdout', 'combined' and 'undeclared'.
    """
    train_solved, train_runs = 0, 0
    holdout_solved, holdout_runs = 0, 0
    undeclared = []

    for r in records:
        scenario_id = (r.get("scenario") if isinstance(r, dict)
                       else (getattr(r, "verdict", {}).get("scenario") or getattr(r, "scenario", "")))
        verified = bool(r.get("verified") if isinstance(r, dict)
                        else (getattr(r, "verdict", {}).get("verified") or getattr(r, "verified", False)))
        if not scenario_id:
            continue
        try:
            sp = split_of(scenario_id)
        except ValueError:
            undeclared.append(scenario_id)
            continue

        if sp == "train":
            train_runs += 1
            if verified:
                train_solved += 1
        elif sp == "holdout":
            holdout_runs += 1
            if verified:
                holdout_solved += 1

    return {
        "train": {"solved": train_solved, "runs": train_runs},
        "holdout": {"solved": holdout_solved, "runs": holdout_runs},
        "combined": {"solved": train_solved + holdout_solved, "runs": train_runs + holdout_runs},
        "undeclared": undeclared,
    }
