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
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger("evals.probes")



class NoLangSmith(RuntimeError):
    """No key, or the SDK is not installed."""


def client():
    """A LangSmith client, or a refusal naming what is missing."""
    # The key lives beside the provider keys; `--push` runs before anything
    # that would have loaded them.
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / "llm_router" / ".env")
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


def example_id(probe) -> str:
    """A probe's example id: the same probe is the same example across pushes.

    Experiments point at example ids. Deleting and re-creating examples on
    every push orphaned the earlier experiments of the explorer probes, which is
    the comparison a behaviour change is judged on.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"probe:{probe.dataset}:{probe.id}"))


def as_example(probe) -> dict:
    """One probe as a dataset example.

    `inputs` is everything needed to reproduce the situation, so an experiment
    is replayable from the dataset alone; `outputs` is the expectation, which is
    what an evaluator compares against. `why` rides along in metadata because a
    failing example whose reason has to be looked up elsewhere gets dismissed.
    """
    return {
        "id": example_id(probe),
        "inputs": {"agent": probe.agent, "prompt": probe.prompt,
                   "files": probe.files, "history": probe.history,
                   "research_dir": probe.research_dir,
                   "through": probe.through},
        "outputs": {"expect": probe.expect},
        "metadata": {"probe_id": probe.id, "why": probe.why.strip(),
                     "kind": probe.kind, "source": probe.source,
                     "reviewed": probe.reviewed},
    }


def push(probes: list, dataset: str) -> dict:
    """Make the dataset match the probes on disk that belong to it.

    Every example is rewritten whole from its file -- inputs, expectation and
    metadata -- under an id derived from the probe's, so nothing in the dataset
    can disagree with the files, and experiments keep pointing at the same
    examples. Examples whose probe is gone are deleted. LangSmith versions the
    dataset on each change, so an old experiment still shows what it ran.
    """
    ls = client()
    mine = [p for p in probes if p.dataset == dataset]
    if not mine:
        raise ValueError(f"no probe on disk belongs to dataset {dataset!r}")
    if ls.has_dataset(dataset_name=dataset):
        target = ls.read_dataset(dataset_name=dataset)
    else:
        target = ls.create_dataset(
            dataset_name=dataset,
            description=("First-decision probes for the free_coding_agent "
                         "agents. Generated from evals/probes/*.yaml -- edit "
                         "the files, not this dataset."))
        logger.info(f"Created dataset {dataset!r}")

    made = [as_example(p) for p in mine]
    there = {str(e.id) for e in ls.list_examples(dataset_id=target.id)}
    new = [e for e in made if e["id"] not in there]
    kept = [e for e in made if e["id"] in there]
    gone = there - {e["id"] for e in made}
    if kept:
        ls.update_examples(dataset_id=target.id, updates=kept)
    if new:
        ls.create_examples(dataset_id=target.id, examples=new)
    if gone:
        ls.delete_examples(example_ids=sorted(gone))
    return {"dataset": dataset, "id": str(target.id), "examples": len(made),
            "created": len(new), "updated": len(kept), "deleted": len(gone)}


def evaluate(probes: list, model, *, floor: int, members: int,
             dataset: str,
             experiment: Optional[str] = None,
             repetitions: int = 1) -> dict:
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
            files=inputs.get("files") or {},
            history=inputs.get("history") or [],
            research_dir=inputs.get("research_dir") or "",
            through=inputs.get("through") or [])
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
                          max_concurrency=1,
                          num_repetitions=max(1, repetitions))
    # Serial, always. The probes share one free-tier pool, and running them at
    # once would make each probe's model mix depend on the others -- the same
    # reason eval runs are serial (evals/run.py).
    return {"dataset": dataset, "experiment": getattr(results, "experiment_name",
                                                      None)}
