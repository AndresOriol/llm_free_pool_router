"""The probes, as a LangSmith dataset — and why the files stay the source.

LangSmith datasets are the right home for a probe *run*: they give a versioned
set of examples, an experiment per run against it, and a UI in which two runs
can be compared example by example. That is exactly what a suite of small
behavioural tests wants and what a terminal table cannot do.

**What they are not is the source of truth.** A probe's expectation is a claim
about how this project's agents should behave, argued from a recorded failure —
`evals/probes/*.yaml` carries the `why` beside the assertion, and it is reviewed
in a diff like any other rule here. A dataset edited in a browser is a rule
nobody reviewed. So the flow is one-way by design: files are pushed to the
dataset, and the dataset is never read back over the files.

Everything below is optional. With no `LANGSMITH_API_KEY` the probes still run
and still report ([evals/probes.py](probes.py)); what is lost is the history and
the comparison, not the test.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("evals.probes")

DEFAULT_DATASET = "free_coding_agent-probes"


class NoLangSmith(RuntimeError):
    """No key, or the SDK is not installed."""


def client():
    """A LangSmith client, or a refusal naming what is missing."""
    if not (os.environ.get("LANGSMITH_API_KEY")
            or os.environ.get("LANGCHAIN_API_KEY")):
        raise NoLangSmith(
            "No LANGSMITH_API_KEY. The probes run without it — this only "
            "affects pushing them to a dataset.")
    try:
        from langsmith import Client
    except ImportError as exc:  # pragma: no cover - the dependency is pinned
        raise NoLangSmith(f"langsmith is not importable: {exc}") from None
    return Client()


def as_example(probe) -> dict:
    """One probe as a dataset example.

    `inputs` is everything needed to reproduce the situation, so an experiment
    is replayable from the dataset alone; `outputs` is the expectation, which is
    what an evaluator compares against. `why` rides along in metadata because a
    failing example whose reason has to be looked up elsewhere gets dismissed.
    """
    return {
        "inputs": {"agent": probe.agent, "prompt": probe.prompt,
                   "files": probe.files},
        "outputs": {"expect": probe.expect},
        "metadata": {"probe_id": probe.id, "why": probe.why.strip()},
    }


def push(probes: list, dataset: str = DEFAULT_DATASET) -> dict:
    """Create or refresh the dataset from the probes on disk.

    Examples are replaced wholesale rather than diffed. A probe's id is stable
    and its expectation is not, so matching on id and updating in place would
    leave a dataset whose examples silently disagree with the files they came
    from — which is the one failure this module is arranged to prevent.
    """
    ls = client()
    if ls.has_dataset(dataset_name=dataset):
        existing = ls.read_dataset(dataset_name=dataset)
        stale = list(ls.list_examples(dataset_id=existing.id))
        if stale:
            ls.delete_examples(example_ids=[e.id for e in stale])
        target = existing
        logger.info(f"Refreshing dataset {dataset!r} ({len(stale)} replaced)")
    else:
        target = ls.create_dataset(
            dataset_name=dataset,
            description=("First-decision probes for the free_coding_agent "
                         "agents. Generated from evals/probes/*.yaml — edit "
                         "the files, not this dataset."))
        logger.info(f"Created dataset {dataset!r}")

    made = [as_example(p) for p in probes]
    ls.create_examples(dataset_id=target.id, examples=made)
    return {"dataset": dataset, "id": str(target.id), "examples": len(made)}


def evaluate(probes: list, model, *, floor: int, members: int,
             dataset: str = DEFAULT_DATASET,
             experiment: Optional[str] = None) -> dict:
    """Run the probes as a LangSmith experiment. Returns a summary.

    The target and the scoring are the same functions the local runner uses
    ([probes.py](probes.py)), so an experiment and a terminal run cannot
    disagree about what passed. What LangSmith adds is where the answer is kept.
    """
    from evals import probes as probes_mod

    ls = client()
    by_id = {p.id: p for p in probes}

    def target(inputs: dict) -> dict:
        probe = probes_mod.Probe(
            id=inputs.get("probe_id") or "probe",
            agent=inputs.get("agent", "code"),
            prompt=inputs.get("prompt", ""),
            files=inputs.get("files") or {})
        return probes_mod.first_decision(probe, model, floor=floor,
                                         members=members)

    def decided_correctly(outputs: dict, reference_outputs: dict,
                          example=None) -> dict:
        """One evaluator, because a probe is one claim.

        Its `comment` carries the reason a check failed -- "expected
        tool='read_file', but first tool was edit_file" -- so the dataset row
        says what went wrong without opening the trace.
        """
        probe_id = ((example.metadata or {}).get("probe_id")
                    if example is not None else None)
        probe = by_id.get(probe_id) or probes_mod.Probe(
            id=probe_id or "probe",
            expect=(reference_outputs or {}).get("expect") or {})
        result = probes_mod.score(probe, outputs or {})
        return {"key": "decided_correctly", "score": bool(result["passed"]),
                "comment": "; ".join(result["reasons"]) or "as expected"}

    logger.info(f"Running {len(probes)} probe(s) against {dataset!r}")
    results = ls.evaluate(target, data=dataset, evaluators=[decided_correctly],
                          experiment_prefix=experiment or "probes",
                          max_concurrency=1)
    # Serial, always. The probes share one free-tier pool, and running them at
    # once would make each probe's model mix depend on the others -- the same
    # reason eval runs are serial (evals/run.py).
    return {"dataset": dataset, "experiment": getattr(results, "experiment_name",
                                                      None)}
