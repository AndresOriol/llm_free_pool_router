"""CLI: python -m evals <command>

    validate [--topic T] [--scenario TAG]     the untouched/gold-patch gate
    run --config NAME [...]                   execute runs and record them
    show [--config NAME]                      summarize recorded runs

Scenarios are data and live in a separate repo (default: the `agent_evals`
sibling of this one); point elsewhere with --scenarios or EVAL_SCENARIOS.
"""

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

from evals import agent_config, run as run_mod, scenario as scenario_mod, verify as verify_mod

REPO = Path(__file__).resolve().parents[1]
CONFIGS = REPO / "evals" / "configs"
RESULTS = REPO / "evals" / "results"
WORKTREES = REPO / "evals" / ".worktrees"
DEFAULT_SCENARIOS = REPO.parent / "agent_evals"


def _scenario_repo(args) -> Path:
    path = Path(args.scenarios or os.environ.get("EVAL_SCENARIOS")
                or DEFAULT_SCENARIOS).resolve()
    if not (path / ".git").exists():
        sys.exit(f"No scenario repo at {path}. Pass --scenarios or set EVAL_SCENARIOS.")
    return path


def _scenarios(repo: Path, args) -> list:
    tags = ([args.scenario] if getattr(args, "scenario", None)
            else scenario_mod.list_scenarios(repo, getattr(args, "topic", "") or ""))
    if not tags:
        sys.exit(f"No scenarios in {repo}. Expected tags like scenario/<topic>/<id>.")
    return [scenario_mod.load(repo, tag) for tag in tags]


def _selected_tasks(scenarios: list, suite: str, tags: list) -> list:
    """(scenario, task) pairs matching the filters."""
    chosen = []
    for scenario in scenarios:
        for task in scenario.tasks:
            if suite and suite not in task.suite:
                continue
            if tags and not set(tags) & set(task.tags + scenario.tags):
                continue
            chosen.append((scenario, task))
    return chosen


def cmd_validate(args) -> int:
    repo = _scenario_repo(args)
    failed = False
    for scenario in _scenarios(repo, args):
        problems = verify_mod.validate(repo, scenario)
        if problems:
            failed = True
            print(f"FAIL {scenario.tag}")
            for problem in problems:
                print(f"       - {problem}")
        else:
            print(f"ok   {scenario.tag}  ({len(scenario.tasks)} task(s))")
    return 1 if failed else 0


def cmd_run(args) -> int:
    repo = _scenario_repo(args)
    configs = [agent_config.load(CONFIGS / f"{name}.yaml", WORKTREES)
               for name in args.config]
    tasks = _selected_tasks(_scenarios(repo, args), args.suite, args.tags or [])
    if not tasks:
        sys.exit("No tasks matched the filters.")

    if not args.skip_validate:
        for scenario in {s.tag: s for s, _ in tasks}.values():
            problems = verify_mod.validate(repo, scenario)
            if problems:
                sys.exit(f"{scenario.tag} fails validation; refusing to run:\n  "
                         + "\n  ".join(problems))

    plan = list(run_mod.interleaved(configs, tasks, args.reps))
    results = Path(args.results)
    print(f"{len(plan)} run(s): {len(configs)} config(s) x {len(tasks)} task(s) "
          f"x {args.reps} rep(s), interleaved, serial.\n")

    for index, (config, scenario, task, rep) in enumerate(plan, 1):
        print(f"[{index}/{len(plan)}] {scenario.topic}/{scenario.id}/{task.id} "
              f"{config.name} rep{rep} ... ", end="", flush=True)
        record = run_mod.execute_run(repo, scenario, task, config, rep, results)
        detail = record["failure_class"] or ""
        print(f"{record['outcome']}{' (' + detail + ')' if detail else ''} "
              f"[{record['wall_time_s']}s, {record['provider_calls']} calls]")
    return 0


def cmd_show(args) -> int:
    rows = run_mod.load_records(Path(args.results))
    if args.config:
        rows = [r for r in rows if r["config"] in args.config]
    if not rows:
        sys.exit("No results yet.")

    grouped = defaultdict(list)
    for row in rows:
        grouped[row["config"]].append(row)

    print(f"{'config':<20}{'runs':>6}{'pass':>7}{'rate':>8}"
          f"{'calls':>8}{'bounces':>9}  failure classes")
    for name, group in sorted(grouped.items()):
        passed = sum(1 for r in group if r["outcome"] == "pass")
        classes = defaultdict(int)
        for row in group:
            if row.get("failure_class"):
                classes[row["failure_class"]] += 1
        summary = ", ".join(f"{k}={v}" for k, v in sorted(classes.items())) or "-"
        print(f"{name:<20}{len(group):>6}{passed:>7}{passed / len(group):>8.0%}"
              f"{_mean(group, 'provider_calls'):>8.1f}"
              f"{_mean(group, 'failover_bounces'):>9.1f}  {summary}")
    return 0


def _mean(rows: list, key: str) -> float:
    values = [r.get(key) or 0 for r in rows]
    return sum(values) / len(values) if values else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(prog="evals")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="check scenarios are well-formed")
    validate.add_argument("--scenarios", default="")
    validate.add_argument("--topic", default="")
    validate.add_argument("--scenario", help="a full tag, e.g. scenario/<topic>/<id>")
    validate.set_defaults(func=cmd_validate)

    run = sub.add_parser("run", help="execute runs")
    run.add_argument("--config", action="append", required=True,
                     help="config name (repeat to compare, interleaved)")
    run.add_argument("--scenarios", default="")
    run.add_argument("--topic", default="")
    run.add_argument("--scenario", help="a full tag, e.g. scenario/<topic>/<id>")
    run.add_argument("--suite", default="")
    run.add_argument("--tags", nargs="*")
    run.add_argument("--reps", type=int, default=3)
    run.add_argument("--skip-validate", action="store_true")
    # Self-tests point this elsewhere so stub runs never land in the real
    # record -- a leaderboard mixing stubbed and measured runs is worse than
    # no leaderboard.
    run.add_argument("--results", default=str(RESULTS))
    run.set_defaults(func=cmd_run)

    show = sub.add_parser("show", help="summarize recorded runs")
    show.add_argument("--config", action="append")
    show.add_argument("--results", default=str(RESULTS))
    show.set_defaults(func=cmd_show)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
