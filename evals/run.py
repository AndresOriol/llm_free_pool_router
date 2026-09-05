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
    """Timestamp first, description after.

    The scenario used to lead, which sorted the results directory by task and
    left "which run is the newest" to be answered by reading every name in it.
    A fixed-width UTC stamp in front makes a plain listing chronological, and
    makes `--since` a prefix comparison.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}_{scenario.id}_{task.id}_{config.name}_r{rep}"


def _execute(config, workdir: Path, prompt: str, trace_path: Path,
             timeout_s: int) -> dict:
    """Launch the agent one-shot with the prompt on stdin."""
    cmd = [part.format(workdir=str(workdir)) for part in config.agent_cmd]
    env = {**os.environ,
           # The pool's ledger, not the throwaway worktree's. A free tier is
           # metered per account, so what a run spends is spent against the same
           # budget every other run draws on -- but `llm_router/usage.py` locates
           # `.usage/` beside its own package, which inside a worktree is the
           # worktree's copy. Runs were writing their usage into the throwaway
           # checkout, which is deleted at the end of the batch, and the quota
           # panel never saw a single one of them.
           "LLM_ROUTER_USAGE_DIR": str((config.repo / "llm_router" / ".usage").resolve()),
           **config.secrets, **config.env,
           "EVAL_TRACE_FILE": str(trace_path)}
    if config.router_config:
        # Read by the agent's loader; lets a configuration swap the model pool,
        # which is the comparison this project most needs to be able to make.
        env["ROUTER_CONFIG"] = str((config.repo / config.router_config).resolve())

    started = time.time()
    # Popen rather than subprocess.run: on a timeout, `run` kills the agent and
    # then drains the pipes, and the drain blocks on any *grandchild* still
    # holding the inherited stdout handle. The agent spawns `python -m pytest`,
    # so that grandchild exists routinely -- and a batch died on exactly this,
    # one run sitting 66 minutes past its 1800s timeout while the trace showed
    # it still calling models. Killing the tree first is what makes the timeout
    # mean something.
    process = subprocess.Popen(cmd, cwd=config.worktree, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               encoding=scenario_mod.ENCODING,
                               errors="replace", env=env)
    try:
        stdout, stderr = process.communicate(input=prompt, timeout=timeout_s)
        return {"stdout": stdout, "stderr": stderr,
                "exit_code": process.returncode, "wall_time_s": time.time() - started,
                "timed_out": False}
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        # Give the drain a bounded second chance; the tree is gone, so the pipes
        # are closed and this returns immediately unless something is very wrong.
        try:
            stdout, stderr = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        return {"stdout": stdout or "",
                "stderr": (stderr or "") + "\n[runner] TIMEOUT",
                "exit_code": -1, "wall_time_s": time.time() - started,
                "timed_out": True}


def _kill_tree(process) -> None:
    """Kill the agent and everything it spawned. Best effort, never raises."""
    if os.name == "nt":
        # taskkill /T is the only thing on Windows that reaches grandchildren;
        # Popen.kill() terminates the named process alone.
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                       capture_output=True)
    try:
        process.kill()
    except OSError:
        pass


def _write(path: Path, text: str) -> None:
    """A run artifact, byte-for-byte as produced."""
    path.write_text(text, encoding="utf-8", newline="\n")


def _outcome(execution: dict, verification: dict, weakened: list) -> str:
    if weakened:
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
    # Copied now rather than at the end, so a run that dies mid-flight still
    # says what it was. With `config_sha` in run.json this is the whole recipe
    # for rebuilding the configuration: `git worktree add --detach <sha>` plus
    # the overrides in this file. Nothing else about the checkout is kept.
    shutil.copyfile(config.path, out_dir / "config.yaml")

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
        # Asked of the seed, before the agent can create one: a scenario that
        # ships no feedback file records `wrote_account: null` rather than a
        # failure to write one.
        has_account = (seed / metrics_mod.ACCOUNT_FILE).is_file()
        before = scenario_mod.hash_files(workdir, scenario.immutable)

        execution = _execute(config, workdir, task.prompt,
                             out_dir / "trace.jsonl", scenario.timeout_s)

        # Pruned first: classifying an integrity change re-runs a protected
        # test against a copy of this tree, and the caches the agent's own
        # pytest left would be copied with it.
        verify_mod.prune_artifacts(workdir)
        changed = verify_mod.check_integrity(before, workdir)
        integrity = verify_mod.classify_integrity(seed, workdir, changed,
                                                  base / "integrity")
        lost_invariants = verify_mod.check_doc_invariants(
            workdir, scenario.doc_invariants)
        patch = verify_mod.make_diff(seed, workdir)
        verification = verify_mod.verify(repo, scenario, workdir, base / "verified")

    # A deleted guarantee is a weakening whether it lived in a test or in a
    # page. `count-and-share` was only ever caught because that scenario happens
    # to pin the sentence with a hidden test; everywhere else in the set,
    # deleting a documented promise was invisible.
    outcome = _outcome(execution, verification,
                       integrity["weakened"] + lost_invariants)
    reference = scenario_mod.read(repo, scenario.tag,
                                  "evaluation/solution.patch") or ""
    measured = metrics_mod.collect(out_dir / "trace.jsonl", patch, reference,
                                   outcome, execution["stderr"],
                                   has_account=has_account,
                                   broken_files=integrity["broken"])

    # newline="\n" everywhere, and on diff.patch it is load-bearing: the default
    # translates each one to a CRLF pair on Windows, and a patch whose context lines
    # carry a CR the target file does not is one `git apply` refuses. Recorded
    # patches were unreplayable for that reason and for the encoding above --
    # `--no-index` output was being decoded as cp1252, so an em dash reached
    # disk as three characters. Both are silent: the file looks like a diff.
    _write(out_dir / "stdout.log", execution["stdout"])
    _write(out_dir / "stderr.log", execution["stderr"])
    _write(out_dir / "diff.patch", patch)
    _write(out_dir / "verify.txt", verification.pop("log"))

    record = {
        "run_id": run_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "scenario": scenario.id, "scenario_tag": scenario.tag,
        "topic": scenario.topic, "withheld_files": len(withheld),
        "difficulty": scenario.difficulty, "category": scenario.category,
        "context_mode": scenario.context_mode,
        "task": task.id, "task_tags": task.tags,
        "config": config.name, "config_sha": config.sha,
        # Recorded rather than inferred from the name: a leaderboard mixing
        # stubbed and measured runs is worse than no leaderboard, and until now
        # the only thing keeping them apart was the operator remembering to
        # pass --results.
        "stub": config.is_stub,
        # Whether the trace arrived, said outright rather than inferred from a
        # zero. Five recorded runs have no trace.jsonl at all -- written before
        # the trace path was made absolute, so the agent wrote it inside its own
        # throwaway worktree -- and every metric summed over it reads 0, which
        # is indistinguishable from a measurement of nothing.
        "traced": (out_dir / "trace.jsonl").is_file(),
        "config_fingerprint": config.fingerprint,
        "rep": rep,
        "outcome": outcome,
        "tampered_files": integrity["weakened"],
        # Protected files the agent changed without weakening: it appended to a
        # suite it was told not to break, and the original assertions still
        # hold. Recorded rather than scored, so the behaviour stops being
        # invisible before anything starts rewarding it.
        "extended_files": integrity["extended"],
        # Protected files the agent left unrunnable. Not tampering: the original
        # assertions still hold against its code, so nothing was removed on
        # purpose -- it wrote something that does not parse. Classified as
        # tooling below, which is what it is.
        "broken_files": integrity["broken"],
        "lost_invariants": lost_invariants,
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
