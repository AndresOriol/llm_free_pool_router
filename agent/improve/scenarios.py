"""Turning a run that went wrong into the eval case that would have caught it.

The set has five scenarios and the repo already says why that is the binding
constraint: seven configurations were run against one L0 scenario and none could
be distinguished from another ([The pass column is noise](../../docs/agents/code.md#the-pass-column-is-noise)).
More scenarios is the only thing that raises the ceiling on what any measurement
here can claim -- including every claim this agent makes about its own fixes.

**The raw material is already the right kind.** `evals/mine.py` established the
principle: a scenario invented to be testable tests what is easy to grade, and
one recovered from a request someone actually made tests what someone actually
needed ([Where a scenario comes from](../../docs/evaluation/scenarios.md#where-a-scenario-comes-from)).
That module mines recorded *Claude Code* sessions and deliberately interprets
nothing, leaving the authoring to a human and a table
([evals/ARCHETYPES.md](../../evals/ARCHETYPES.md)).

This is the same idea pointed at a different corpus and taken one step further.
The corpus is the free agents' **own** recorded runs, and the step is the
authoring: a run where the agent failed is a description of a test it would have
failed, and the improvement agent is already reading those runs closely enough
to say what the failure was.

**What this does not do is build the scenario.** A scenario lives in the
`agent_evals` repository, needs a seed codebase, hidden tests and a gold patch,
and getting it wrong in the direction of "leaks its own tests" would score every
configuration far too well and read as a win rather than as a bug
([Anatomy](../../docs/evaluation/scenarios.md#anatomy)). So what is produced here is a
*brief*: the judgement, written down, in the shape the builder needs -- and the
building is a coding session against that repository, with the seed, the tests
and the patch written by an agent that can run them.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from agent.improve import issues as issues_mod

logger = logging.getLogger("harness.improve")

# Beside the ledger and the runs it is drawn from.
DRAFTS_DIR = Path("evals") / "results" / "scenarios"

# `scenario.yaml`'s own vocabulary, so a draft cannot invent a category the
# runner will reject ([`scenario.yaml`](../../docs/evaluation/scenarios.md#scenarioyaml)).
CATEGORIES = ("bugfix", "feature", "refactor", "tests", "ambiguous", "trap")
DIFFICULTIES = ("L0", "L1", "L2", "L3")


@dataclass
class Draft:
    """One scenario, described but not built."""

    id: str
    title: str
    source_run: str = ""
    archetype: str = ""             # which shape in ARCHETYPES.md, if any
    category: str = "bugfix"
    difficulty: str = "L1"
    seed: str = ""                  # the code state the agent should be given
    prompt: str = ""                # the exact text to pipe to the agent
    challenge: str = ""             # what makes it hard; first line is the summary
    fail_to_pass: str = ""          # what must go from failing to passing
    pass_to_pass: str = ""          # what must keep working
    traps: str = ""                 # answers that pass without being right
    invariants: str = ""            # immutable files / doc sentences to protect
    provenance: dict = field(default_factory=dict)   # machine-read, not claimed
    created: str = ""

    def render(self) -> str:
        """The brief, in the order the builder needs it.

        Deliberately the four headings of `evaluation/scenario.md` -- The seed,
        The task, The challenge, What it checks
        ([`evaluation/scenario.md`, the page for a human](../../docs/evaluation/scenarios.md#evaluationscenariomd-the-page-for-a-human))
        -- plus the two things only the source run can supply. A builder that
        follows this is filling in a page the set already has a shape for,
        rather than being asked to invent one.
        """
        lines = [
            f"# {self.id} — {self.title}", "",
            f"*Drafted from run `{self.source_run or 'unknown'}` on "
            f"{self.created[:19]}. This is a brief, not a scenario: nothing "
            f"here has been built or run.*", "",
            "| | |", "| --- | --- |",
            f"| category | `{self.category}` |",
            f"| difficulty | `{self.difficulty}` |",
            f"| archetype | {self.archetype or '—'} |",
        ]
        for key, value in sorted(self.provenance.items()):
            lines.append(f"| {key} | {value} |")
        lines.append("")

        for heading, body, note in (
            ("The seed", self.seed,
             "The code state the agent is given. It must look like a real "
             "project, not a fixture."),
            ("The task", self.prompt,
             "The exact text piped to the agent's stdin — `tasks/<id>.md`."),
            ("The challenge", self.challenge,
             "The first line is lifted verbatim into the catalogue, so it is "
             "one self-contained sentence."),
            ("What it checks", "", ""),
        ):
            lines += [f"## {heading}", ""]
            if note:
                lines += [f"*{note}*", ""]
            if body:
                lines += [body, ""]

        lines += ["**fail_to_pass** — must go from failing to passing:", "",
                  self.fail_to_pass or "*(not stated — the draft is incomplete)*",
                  "", "**pass_to_pass** — must stay passing:", "",
                  self.pass_to_pass or "*(not stated)*", ""]
        if self.invariants:
            lines += ["**Protected** — `immutable` files, or `doc_invariants` "
                      "sentences:", "", self.invariants, ""]
        if self.traps:
            lines += ["## What passes without being right", "",
                      "*The answers a run can give that satisfy the letter of "
                      "the task and miss it. If there are none, this is not "
                      "worth building.*", "", self.traps, ""]

        lines += [
            "## Building it", "",
            "This scenario does not exist yet. Building it means, in the "
            "`agent_evals` repository: a topic branch holding the seed at its "
            "root, `tasks/<task-id>.md` with the prompt above, and under "
            "`evaluation/` — which is withheld from the agent — the tests named "
            "above, `scenario.md`, `criteria.md`, a `solution.patch` that makes "
            "`fail_to_pass` pass, and `scenario.yaml` naming both test sets.",
            "",
            "Read `docs/evaluation/scenarios.md` before starting, and run `python -m "
            "evals validate --scenario <tag>` when finished: the gate checks "
            "that the untouched seed fails `fail_to_pass`, that the gold patch "
            "makes it pass, and that nothing under `evaluation/` leaked into "
            "the workdir. **A scenario that ships its own tests scores every "
            "configuration far too well and reads as a win rather than a bug.**",
        ]
        return "\n".join(lines)


class DraftStore:
    """Every drafted scenario, one Markdown file each."""

    def __init__(self, root: Path) -> None:
        self.directory = Path(root) / DRAFTS_DIR

    def path(self, draft_id: str) -> Path:
        return self.directory / f"{draft_id}.md"

    def save(self, draft: Draft) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.path(draft.id)
        path.write_text(draft.render(), encoding="utf-8")
        return path

    def list(self) -> list:
        if not self.directory.is_dir():
            return []
        return sorted(p.stem for p in self.directory.glob("*.md"))


def provenance(record) -> dict:
    """What the source run says about itself, read rather than claimed.

    A draft's most forgeable field is "this really happened". These come off
    `run.json` and the diff, so a brief cannot describe a failure the run did
    not have.
    """
    if record is None:
        return {}
    verdict = record.verdict or {}
    found = {"run kind": record.kind}
    for key, label in (("outcome", "outcome"), ("failure_class", "failure"),
                       ("scenario", "seen on scenario"), ("config", "config"),
                       ("difficulty", "source difficulty")):
        if verdict.get(key):
            found[label] = f"`{verdict[key]}`"
    if verdict.get("f2p_total"):
        found["hidden tests"] = (f"{verdict.get('f2p_passed', '?')}/"
                                 f"{verdict['f2p_total']} fail_to_pass")
    if verdict.get("diff_files"):
        found["files it touched"] = ", ".join(
            f"`{p}`" for p in list(verdict["diff_files"])[:6])
    return found


def draft(store: DraftStore, fields: dict, record=None) -> Draft:
    """Validate and persist one draft. Raises ValueError on an unusable one.

    The validation is thin on purpose -- the judgement is the model's and this
    is not a second opinion about it. What it refuses is a draft that could not
    become a scenario at all: no prompt to pipe, or no statement of what would
    go from failing to passing, which is the whole acceptance contract
    ([`scenario.yaml`](../../docs/evaluation/scenarios.md#scenarioyaml)).
    """
    title = str(fields.get("title") or "").strip()
    if not title:
        raise ValueError("a draft needs a title")

    draft_id = str(fields.get("id") or issues_mod.slug(title))
    category = str(fields.get("category") or "bugfix").strip().lower()
    if category not in CATEGORIES:
        raise ValueError(f"category must be one of {', '.join(CATEGORIES)}")
    difficulty = str(fields.get("difficulty") or "L1").strip().upper()
    if difficulty not in DIFFICULTIES:
        raise ValueError(f"difficulty must be one of {', '.join(DIFFICULTIES)}")

    if not str(fields.get("prompt") or "").strip():
        raise ValueError(
            "a draft needs the exact `prompt` the agent would be given; "
            "without it there is no task, only a topic")
    if not str(fields.get("fail_to_pass") or "").strip():
        raise ValueError(
            "a draft needs `fail_to_pass`: what must go from failing to "
            "passing. A scenario with no such claim cannot decide anything")

    made = Draft(
        id=draft_id, title=title,
        source_run=str(fields.get("source_run") or
                       (record.id if record is not None else "")),
        archetype=str(fields.get("archetype") or ""),
        category=category, difficulty=difficulty,
        seed=str(fields.get("seed") or ""),
        prompt=str(fields.get("prompt") or ""),
        challenge=str(fields.get("challenge") or ""),
        fail_to_pass=str(fields.get("fail_to_pass") or ""),
        pass_to_pass=str(fields.get("pass_to_pass") or ""),
        traps=str(fields.get("traps") or ""),
        invariants=str(fields.get("invariants") or ""),
        provenance=provenance(record),
        created=issues_mod.now())
    store.save(made)
    return made


def builder_brief(draft: Draft, scenario_repo: str) -> str:
    """The request handed to a coding session bound to the scenario repo."""
    return (
        f"Build the eval scenario described below in this repository "
        f"({scenario_repo}). Read `docs/evaluation/scenarios.md` in the harness repo "
        f"first if it is reachable; otherwise follow the structure the brief "
        f"states.\n\n"
        f"Work on a topic branch. When you are done, the untouched seed must "
        f"fail every `fail_to_pass` test and the gold patch must make them "
        f"pass — build the patch and check that, rather than assuming it.\n\n"
        f"Nothing under `evaluation/` may be reachable from the workdir the "
        f"agent is given.\n\n---\n\n{draft.render()}")
