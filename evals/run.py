"""One run: materialize, execute, verify, check integrity, record.

Runs are serial by design. Parallel runs contend for the same free-tier pool,
so each one's model mix would depend on the others -- which destroys the only
thing the results are for.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from evals import metrics as metrics_mod
from evals import scenario as scenario_mod
from evals import verify as verify_mod



def _run_id(scenario, task, config, rep) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{scenario.id}_{task.id}_{config.name}_r{rep}_{stamp}"


def _execute(config, workdir: Path, prompt: str, trace_path: Path,
             timeout_s: int) -> dict:
    """Launch the agent one-shot with the prompt on stdin."""
    cmd = [part.format(workdir=str(workdir)) for part in config.agent_cmd]
    env = {**os.environ, **config.secrets, **config.env,
           "EVAL_TRACE_FILE": str(trace_path)}
    if config.router_config:
        # Read by the agent's loader; lets a configuration swap the model pool,
        # which is the comparison this project most needs to be able to make.
        env["ROUTER_CONFIG"] = str((config.repo / config.router_config).resolve())

    started = time.time()
    try:
        result = subprocess.run(cmd, cwd=config.worktree, input=prompt, text=True,
                                capture_output=True, errors="replace",
                                timeout=timeout_s, env=env)
        return {"stdout": result.stdout, "stderr": result.stderr,
                "exit_code": result.returncode, "wall_time_s": time.time() - started,
                "timed_out": False}
    except subprocess.TimeoutExpired as exc:
        return {"stdout": exc.stdout or "", "stderr": (exc.stderr or "") + "\n[runner] TIMEOUT",
                "exit_code": -1, "wall_time_s": time.time() - started,
                "timed_out": True}


def _collect_session_artifacts(workdir: Path, out_dir: Path) -> None:
    """Keep what a session wrote about itself. No-op for a task-shaped agent."""
    state = workdir / ".harness"
    journal = state / "journal.jsonl"
    if journal.is_file():
        shutil.copyfile(journal, out_dir / "journal.jsonl")
    reports = sorted((state / "reports").glob("session-*.md"))
    if reports:
        shutil.copyfile(reports[-1], out_dir / "rationale.md")
    # The per-turn transcript: prompt in, reply out. It is the only artifact
    # that says what a role was given, so a post-mortem that loses it can
    # report where a run went wrong but not why.
    steps = state / "steps"
    if steps.is_dir():
        shutil.copytree(steps, out_dir / "steps", dirs_exist_ok=True)


def _outcome(execution: dict, verification: dict, tampered: list) -> str:
    if tampered:
        return "tampered"
    if execution["timed_out"]:
        return "timeout"
    if verification["verified"]:
        return "pass"
    if execution["exit_code"] != 0:
        return "crash"
    return "fail"


def execute_run(repo: Path, scenario, task, config, rep: int,
                results_dir: Path) -> dict:
    run_id = _run_id(scenario, task, config, rep)
    # Absolute: the agent subprocess runs with cwd set to its worktree, so a
    # relative trace path would make it write the trace inside the worktree
    # and leave every trace-derived metric silently reading zero.
    results_dir = Path(results_dir).resolve()
    out_dir = results_dir / "runs" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        seed = base / "seed"
        workdir = base / "work"

        withheld = scenario_mod.materialize_code(repo, scenario.tag, seed)
        # Assert the split rather than trusting it. A scenario that leaked its
        # own tests would score every configuration far too well, and would
        # look like a win rather than a bug.
        for hidden in scenario_mod.HIDDEN:
            if (seed / hidden).exists():
                raise RuntimeError(
                    f"{scenario.tag} leaked {hidden!r} into the workdir")
        shutil.copytree(seed, workdir)
        before = scenario_mod.hash_files(workdir, scenario.immutable)

        execution = _execute(config, workdir, task.prompt,
                             out_dir / "trace.jsonl", scenario.timeout_s)

        tampered = verify_mod.check_integrity(before, workdir)
        # Before pruning: a session writes its rationale and journal into the
        # workdir's .harness/, which prune_artifacts deletes. Those two files
        # are the deliverable under a review model where nobody reads the code,
        # so losing them would leave the judge grading the diff alone.
        _collect_session_artifacts(workdir, out_dir)
        verify_mod.prune_artifacts(workdir)
        patch = verify_mod.make_diff(seed, workdir)
        verification = verify_mod.verify(repo, scenario, workdir, base / "verified")

    outcome = _outcome(execution, verification, tampered)
    reference = scenario_mod.read(repo, scenario.tag,
                                  "evaluation/solution.patch") or ""
    measured = metrics_mod.collect(out_dir / "trace.jsonl", patch, reference,
                                   outcome, execution["stderr"])

    (out_dir / "stdout.log").write_text(execution["stdout"], encoding="utf-8")
    (out_dir / "stderr.log").write_text(execution["stderr"], encoding="utf-8")
    (out_dir / "diff.patch").write_text(patch, encoding="utf-8")
    (out_dir / "verify.txt").write_text(verification.pop("log"), encoding="utf-8")

    record = {
        "run_id": run_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "scenario": scenario.id, "scenario_tag": scenario.tag,
        "topic": scenario.topic, "withheld_files": len(withheld),
        "difficulty": scenario.difficulty, "category": scenario.category,
        "context_mode": scenario.context_mode,
        "task": task.id, "task_tags": task.tags,
        "config": config.name, "config_sha": config.sha,
        "config_fingerprint": config.fingerprint,
        "rep": rep,
        "outcome": outcome,
        "tampered_files": tampered,
        "exit_code": execution["exit_code"],
        "wall_time_s": round(execution["wall_time_s"], 1),
        **verification,
        **measured,
    }
    # One file per run, no shared append-only index: an index would conflict on
    # every merge of a configuration branch, and it is derivable anyway.
    (out_dir / "run.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def load_records(results_dir: Path) -> list:
    """Every recorded run, newest last."""
    paths = sorted(Path(results_dir).glob("runs/*/run.json"))
    return [json.loads(p.read_text(encoding="utf-8")) for p in paths]


def interleaved(configs: list, tasks: list, reps: int):
    """(config, task, rep) ordered so configs alternate.

    Quota drifts as a batch runs. Executing all of A then all of B would hand
    one configuration the fresh pool and the other the exhausted one, and the
    difference would be read as a result.
    """
    for rep in range(1, reps + 1):
        for scenario, task in tasks:
            for config in configs:
                yield config, scenario, task, rep
