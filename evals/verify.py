"""Verification, integrity, and the scenario validation gate.

Hidden tests are applied to a *copy* of the finished workdir, so they hold even
if the agent deleted or rewrote the visible ones. Each entry in fail_to_pass /
pass_to_pass is run as its own pytest invocation: slower than one batched call
by a process start per entry, but it gives an unambiguous per-entry result
without parsing pytest's console summary, which is not a stable interface.
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from evals import scenario as scenario_mod

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


ARTIFACT_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def prune_artifacts(root: Path) -> None:
    """Delete test/build caches before diffing.

    The agent is told to run the tests, which creates .pytest_cache and
    __pycache__ in its workdir. Left in, they dominate the diff -- inflating
    diff_lines and files_touched, and telling the judge the agent sprayed
    changes across the tree when it changed one function.
    """
    for path in sorted(root.rglob("*"), key=lambda p: -len(p.parts)):
        if path.is_dir() and path.name in ARTIFACT_DIRS:
            shutil.rmtree(path, ignore_errors=True)
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


def verify(repo: Path, scenario, work_dir: Path, dest: Path) -> dict:
    """Run both test sets against the agent's output."""
    root = prepare(repo, scenario, work_dir, dest)
    f2p_passed, f2p_total, f2p_log = run_tests(root, scenario.fail_to_pass)
    p2p_passed, p2p_total, p2p_log = run_tests(root, scenario.pass_to_pass)

    return {
        "f2p_passed": f2p_passed, "f2p_total": f2p_total,
        "p2p_passed": p2p_passed, "p2p_total": p2p_total,
        "f2p_ratio": f2p_passed / f2p_total if f2p_total else 1.0,
        "p2p_ratio": p2p_passed / p2p_total if p2p_total else 1.0,
        # Both conditions, not one: fixing the bug while breaking something
        # else is not a pass.
        "verified": f2p_passed == f2p_total and p2p_passed == p2p_total,
        "log": f"== fail_to_pass ==\n{f2p_log}\n\n== pass_to_pass ==\n{p2p_log}",
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
                                 input=patch, text=True, capture_output=True)
        if applied.returncode != 0:
            problems.append(f"reference patch does not apply: {applied.stderr.strip()}")
            return problems

        solved = verify(repo, scenario, gold, base / "solved")
        if not solved["verified"]:
            problems.append(
                f"reference solution does not pass: f2p "
                f"{solved['f2p_passed']}/{solved['f2p_total']}, p2p "
                f"{solved['p2p_passed']}/{solved['p2p_total']}")
    return problems
