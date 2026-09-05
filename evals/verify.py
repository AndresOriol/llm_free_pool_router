"""Verification, integrity, and the scenario validation gate.

Hidden tests are applied to a *copy* of the finished workdir, so they hold even
if the agent deleted or rewrote the visible ones. Each entry in fail_to_pass /
pass_to_pass is run as its own pytest invocation: slower than one batched call
by a process start per entry, but it gives an unambiguous per-entry result
without parsing pytest's console summary, which is not a stable interface.
"""

import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from evals import catalog as catalog_mod, scenario as scenario_mod

_DIFF_HEADER = re.compile(r"^diff --git a/[^/]+/(.*) b/[^/]+/(.*)$", re.MULTILINE)
_DIFF_PREFIX = re.compile(r"^(--- a|\+\+\+ b)/[^/]+/", re.MULTILINE)


def _normalize(patch_text: str) -> str:
    """Strip the comparison dir out of every path `git diff --no-index` emits.

    The `diff --git` header needs rewriting too, not just the ---/+++ lines:
    `git apply` reads the header names, so a doubled prefix left there makes
    the patch apply to the wrong path.
    """
    patch_text = _DIFF_HEADER.sub(r"diff --git a/\1 b/\2", patch_text)
    return _DIFF_PREFIX.sub(r"\1/", patch_text)


ARTIFACT_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                 # An agent that commits its own work initializes a repo to
                 # commit into. That is its bookkeeping, not the change under
                 # test, and left in it would dominate the diff the judge reads.
                 ".git"}


def _force_writable(func, path, _exc) -> None:
    """Retry a deletion after clearing the read-only bit.

    Git marks everything under `.git/objects` read-only, and on Windows that
    makes it undeletable. `rmtree(..., ignore_errors=True)` swallowed the
    failure, so a session's own repository survived the prune and every git
    object landed in the diff -- inflating `files_touched`, corrupting the
    `touched` set the failure taxonomy is derived from, and burying the actual
    change in a wall of binary blobs.
    """
    os.chmod(path, stat.S_IWRITE)
    func(path)


def prune_artifacts(root: Path) -> None:
    """Delete test/build caches before diffing.

    The agent is told to run the tests, which creates .pytest_cache and
    __pycache__ in its workdir. Left in, they dominate the diff -- inflating
    diff_lines and files_touched, and telling the judge the agent sprayed
    changes across the tree when it changed one function.
    """
    for path in sorted(root.rglob("*"), key=lambda p: -len(p.parts)):
        if path.is_dir() and path.name in ARTIFACT_DIRS:
            shutil.rmtree(path, onexc=_force_writable)
        elif path.is_file() and path.suffix == ".pyc":
            path.unlink(missing_ok=True)


def make_diff(seed_dir: Path, work_dir: Path) -> str:
    """Unified diff of what the agent changed, with plain a/ b/ prefixes."""
    result = subprocess.run(
        ["git", "diff", "--no-index", "--no-color", seed_dir.name, work_dir.name],
        cwd=seed_dir.parent, capture_output=True, text=True, errors="replace")
    # --no-index exits 1 when there are differences; only >1 is a real error.
    if result.returncode > 1:
        raise RuntimeError(f"git diff failed: {result.stderr.strip()}")
    return _normalize(result.stdout)


def _pytest(node_id: str, cwd: Path, timeout: int = 300) -> tuple:
    try:
        result = subprocess.run(["python", "-m", "pytest", node_id, "-q",
                                 "--no-header", "-p", "no:cacheprovider"],
                                cwd=cwd, capture_output=True, text=True,
                                errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"{node_id}: TIMEOUT"
    # Exit 5 is "no tests collected" -- a deleted test file, which is a failure
    # here, not a skip.
    return result.returncode == 0, f"{node_id}: exit {result.returncode}\n{result.stdout}"


def run_tests(root: Path, entries: list) -> tuple:
    """(passed_count, total, log) for one test set."""
    passed, log = 0, []
    for entry in entries:
        ok, output = _pytest(entry, root)
        passed += ok
        log.append(output)
    return passed, len(entries), "\n".join(log)


def prepare(repo: Path, scenario, work_dir: Path, dest: Path) -> Path:
    """A copy of the finished workdir with evaluation/ overlaid.

    Overlaid only now, after the agent is done: during the run the workdir
    never contains the tests it is scored with.
    """
    shutil.copytree(work_dir, dest, dirs_exist_ok=True)
    scenario_mod.materialize_hidden(repo, scenario.tag, "evaluation",
                                    dest / "evaluation")
    return dest


def _imports(root: Path, entry_point: str) -> bool:
    """Does the thing the scenario names actually import?

    The first rung of the ladder: a run that produced something executable is a
    different outcome from one that produced nothing, and on a generative task
    that is most of what the early months have to say.
    """
    if not entry_point:
        return True
    try:
        result = subprocess.run(["python", "-c", f"import {entry_point}"],
                                cwd=root, capture_output=True, text=True,
                                timeout=60)
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0


def verify(repo: Path, scenario, work_dir: Path, dest: Path) -> dict:
    """Run both test sets against the agent's output."""
    root = prepare(repo, scenario, work_dir, dest)
    f2p_passed, f2p_total, f2p_log = run_tests(root, scenario.fail_to_pass)
    p2p_passed, p2p_total, p2p_log = run_tests(root, scenario.pass_to_pass)

    # The tier ladder. Each rung is a deterministic, zero-token check, and each
    # is recorded separately so the months before a first pass stay legible
    # (design/generative-scenarios.md 3.2).
    contract_passed, contract_total, contract_log = run_tests(
        root, scenario.contract_tests)

    return {
        "f2p_passed": f2p_passed, "f2p_total": f2p_total,
        "p2p_passed": p2p_passed, "p2p_total": p2p_total,
        "f2p_ratio": f2p_passed / f2p_total if f2p_total else 1.0,
        "p2p_ratio": p2p_passed / p2p_total if p2p_total else 1.0,
        "tier_imports": _imports(root, scenario.entry_point),
        "tier_contract": contract_passed == contract_total,
        "tier_behaviour": f2p_passed == f2p_total,
        "tier_intact": p2p_passed == p2p_total,
        # Both conditions, not one: fixing the bug while breaking something
        # else is not a pass.
        "verified": f2p_passed == f2p_total and p2p_passed == p2p_total,
        "log": (f"== fail_to_pass ==\n{f2p_log}\n\n== pass_to_pass ==\n{p2p_log}"
                + (f"\n\n== contract ==\n{contract_log}" if contract_log else "")),
    }


def check_integrity(before: dict, work_dir: Path) -> list:
    """Immutable files the agent modified or deleted."""
    after = scenario_mod.hash_files(work_dir, list(before))
    return sorted(path for path, digest in before.items() if after.get(path) != digest)


def validate(repo: Path, scenario) -> list:
    """The empty-patch / gold-patch gate. Returns a list of problems.

    Run on every suite execution, not just at authoring time: dependency drift
    that makes a pass_to_pass test fail on the seed would otherwise surface as
    every configuration regressing at once.
    """
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)

        seed = base / "seed"
        scenario_mod.materialize_code(repo, scenario.tag, seed)

        for rel in scenario.immutable:
            if not (seed / rel).is_file():
                problems.append(f"immutable file not in the code state: {rel}")

        # A test set that is empty passes every run without measuring anything,
        # and every check below reads clean on it: 0 > 0 is False, 0 != 0 is
        # False, and the reference solution "passes". Misspell `fail_to_pass`
        # and the scenario scores every configuration a free pass, silently.
        if not scenario.fail_to_pass:
            problems.append(
                "no fail_to_pass tests -- every run would pass without being "
                "measured; check the key is spelled right in scenario.yaml")

        # The catalogue on the scenario repo's master repeats the withheld
        # material for a human. That is only safe while no scenario commit
        # carries it, since `docs/` is visible seed content in several
        # scenarios and would be handed straight to the agent. The catalogue
        # is a directory of pages, so the check is on the directories: a
        # single scenario's page leaks that scenario's answer whole.
        for page in catalog_mod.leaked_pages(seed):
            problems.append(
                f"{page} is in the code state -- the catalogue would be "
                "materialized into the agent's workdir")

        # Empty patch: the bug must be present and nothing else broken.
        empty = verify(repo, scenario, seed, base / "empty")
        if empty["f2p_passed"] > 0:
            problems.append(
                f"{empty['f2p_passed']}/{empty['f2p_total']} fail_to_pass tests "
                "already pass on the untouched code -- the task is partly pre-solved")
        if empty["p2p_passed"] != empty["p2p_total"]:
            problems.append(
                f"only {empty['p2p_passed']}/{empty['p2p_total']} pass_to_pass "
                "tests pass on the untouched code -- the scenario is broken")

        # Gold patch: the task must be solvable.
        patch = scenario_mod.read(repo, scenario.tag, "evaluation/solution.patch")
        if not patch:
            problems.append("no evaluation/solution.patch")
            return problems

        gold = base / "gold"
        scenario_mod.materialize_code(repo, scenario.tag, gold)
        applied = subprocess.run(["git", "apply", "-p1", "-"], cwd=gold,
                                 input=patch, capture_output=True,
                                 encoding=scenario_mod.ENCODING)
        if applied.returncode != 0:
            problems.append(f"reference patch does not apply: {applied.stderr.strip()}")
            return problems

        solved = verify(repo, scenario, gold, base / "solved")
        if not solved["verified"]:
            problems.append(
                f"reference solution does not pass: f2p "
                f"{solved['f2p_passed']}/{solved['f2p_total']}, p2p "
                f"{solved['p2p_passed']}/{solved['p2p_total']}")

        # On a generative scenario the empty-patch gate degenerates -- the
        # module does not exist, so every fail_to_pass test fails at import and
        # the gate proves nothing about test quality. A second implementation,
        # written to the same spec and deliberately different in structure,
        # carries that weight instead: if it fails, the suite is coupled to an
        # implementation rather than to the requirement
        # (design/generative-scenarios.md 3.1).
        if scenario.category == "generative":
            problems += _check_alternative(repo, scenario, base)
    return problems


def _check_alternative(repo: Path, scenario, base: Path) -> list:
    """The second-implementation gate. Generative scenarios only."""
    alt = scenario_mod.read(repo, scenario.tag, "evaluation/solution_alt.patch")
    if not alt:
        return ["category: generative requires evaluation/solution_alt.patch -- "
                "a second implementation is what replaces the empty-patch gate"]

    tree = base / "alt"
    scenario_mod.materialize_code(repo, scenario.tag, tree)
    applied = subprocess.run(["git", "apply", "-p1", "-"], cwd=tree,
                             input=alt, capture_output=True,
                             encoding=scenario_mod.ENCODING)
    if applied.returncode != 0:
        return [f"alternative patch does not apply: {applied.stderr.strip()}"]

    solved = verify(repo, scenario, tree, base / "alt-solved")
    if not solved["verified"]:
        return [f"alternative implementation does not pass: f2p "
                f"{solved['f2p_passed']}/{solved['f2p_total']}, p2p "
                f"{solved['p2p_passed']}/{solved['p2p_total']} -- the hidden "
                "suite is coupled to the reference implementation"]
    return []
