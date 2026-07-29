"""Reading scenarios out of the scenario repo.

Scenarios are *data*, so they live in a separate repo (`agent_evals` by
default) where a real codebase state can be versioned as a real codebase
state. This module never writes to that repo: a run materializes a scenario
into a throwaway directory, so everything the agent did is discarded by
deleting a temp dir. Nothing needs reverting, and the agent never has commit
access to a scenario.

Layout there: one **branch per topic**, and each **commit on it is a
scenario**, named by a tag:

    topic/<topic>                  the topic's line of scenarios
    scenario/<topic>/<id>          tag on the commit that is that scenario

Inside a scenario commit:

    <the code, at the root>        materialized into the agent's workdir
    tasks/*.md                     prompts (fed on stdin, never materialized)
    evaluation/                    criteria, external tests, reference solution
    scenario.yaml                  metadata

Everything named in HIDDEN is withheld from the workdir. `evaluation/` is the
one that matters: it holds the tests the run is scored with, and an agent that
can read them can write code that satisfies them without solving anything.
`scenario.yaml` is withheld too, because it names those tests.
"""

import hashlib
import io
import subprocess
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

# Withheld from the agent's workdir. Anything not listed here is "the code".
HIDDEN = ("tasks", "evaluation", "scenario.yaml")


@dataclass
class Task:
    id: str
    scenario_id: str
    prompt: str
    suite: list
    tags: list
    judge_notes: str = ""


@dataclass
class Scenario:
    id: str
    topic: str
    tag: str
    title: str
    category: str
    difficulty: str
    tags: list
    context_mode: str
    immutable: list
    fail_to_pass: list
    pass_to_pass: list
    timeout_s: int
    tasks: list = field(default_factory=list)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def read(repo: Path, tag: str, path: str) -> Optional[str]:
    """One file out of a scenario commit, without checking anything out."""
    result = subprocess.run(["git", "-C", str(repo), "show", f"{tag}:{path}"],
                            capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else None


def list_scenarios(repo: Path, topic: str = "") -> list:
    """Every scenario tag, optionally limited to one topic."""
    pattern = f"scenario/{topic}/*" if topic else "scenario/*/*"
    return sorted(_git(repo, "tag", "--list", pattern).split())


def list_topics(repo: Path) -> list:
    return sorted({tag.split("/")[1] for tag in list_scenarios(repo)})


def _parse_task(text: str, task_id: str, scenario_id: str) -> Task:
    """Front matter, then '## Prompt' / '## Judge notes' sections."""
    meta, body = {}, text
    if text.startswith("---"):
        _, raw, body = text.split("---", 2)
        meta = yaml.safe_load(raw) or {}

    sections, current = {}, None
    for line in body.splitlines():
        if line.startswith("## "):
            current = line[3:].strip().lower()
            sections[current] = []
        elif current:
            sections[current].append(line)
    joined = {k: "\n".join(v).strip() for k, v in sections.items()}

    return Task(
        id=meta.get("id", task_id),
        scenario_id=scenario_id,
        prompt=joined.get("prompt", "").strip(),
        suite=meta.get("suite", []),
        tags=meta.get("tags", []),
        judge_notes=joined.get("judge notes", ""),
    )


def load(repo: Path, tag: str) -> Scenario:
    raw = read(repo, tag, "scenario.yaml")
    if raw is None:
        raise RuntimeError(f"{tag} has no scenario.yaml")
    meta = yaml.safe_load(raw)
    _, topic, scenario_id = tag.split("/", 2)

    scenario = Scenario(
        id=meta.get("id", scenario_id),
        topic=topic,
        tag=tag,
        title=meta.get("title", ""),
        category=meta.get("category", "unknown"),
        difficulty=meta.get("difficulty", "unknown"),
        tags=meta.get("tags", []),
        context_mode=meta.get("context_mode", "none"),
        immutable=meta.get("immutable", []),
        fail_to_pass=meta.get("fail_to_pass", []),
        pass_to_pass=meta.get("pass_to_pass", []),
        timeout_s=meta.get("timeout_s", 900),
    )

    for path in _git(repo, "ls-tree", "--name-only", tag, "tasks/").split():
        text = read(repo, tag, path)
        if text is not None:
            scenario.tasks.append(_parse_task(text, Path(path).stem, scenario.id))
    return scenario


def _archive(repo: Path, tag: str, subdir: str = "") -> bytes:
    args = ["git", "-C", str(repo), "archive", "--format=tar", tag]
    if subdir:
        args.append(subdir)
    result = subprocess.run(args, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"git archive {tag}:{subdir} failed: "
                           f"{result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def materialize_code(repo: Path, tag: str, dest: Path) -> list:
    """Extract the code state into dest, withholding everything in HIDDEN.

    Returns what was withheld so a caller can assert the split actually
    happened rather than trusting that it did.
    """
    dest.mkdir(parents=True, exist_ok=True)
    withheld = []
    with tarfile.open(fileobj=io.BytesIO(_archive(repo, tag))) as tar:
        for member in tar.getmembers():
            if member.name.split("/")[0] in HIDDEN:
                withheld.append(member.name)
                continue
            tar.extract(member, dest, filter="data")
    return withheld


def materialize_hidden(repo: Path, tag: str, subdir: str, dest: Path) -> None:
    """Extract one withheld subtree, for scoring after the agent is done."""
    dest.mkdir(parents=True, exist_ok=True)
    prefix = subdir.rstrip("/") + "/"
    with tarfile.open(fileobj=io.BytesIO(_archive(repo, tag, subdir))) as tar:
        for member in tar.getmembers():
            if not member.name.startswith(prefix):
                continue
            member.name = member.name[len(prefix):]
            if member.name:
                tar.extract(member, dest, filter="data")


def hash_files(root: Path, relative_paths: list) -> dict:
    """sha256 per path; a missing file hashes to None so deletion is caught."""
    return {rel: (hashlib.sha256((root / rel).read_bytes()).hexdigest()
                  if (root / rel).is_file() else None)
            for rel in relative_paths}
