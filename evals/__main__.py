"""CLI: python -m evals <command>

    validate [--topic T] [--scenario TAG]     the untouched/gold-patch gate
    index [--check]                           rebuild the scenario repo docs
    run --config NAME [...]                   execute runs and record them
    show [--config NAME]                      summarize recorded runs
    bundle [--config NAME] [--out FILE]       collect a batch's evidence for J2

Scenarios are data and live in a separate repo (default: the `agent_evals`
sibling of this one); point elsewhere with --scenarios or EVAL_SCENARIOS.
"""

import argparse
import contextlib
import os
import sys
from collections import defaultdict
from pathlib import Path

from evals import (agent_config, bundle as bundle_mod, catalog as catalog_mod,
                   run as run_mod, scenario as scenario_mod, verify as verify_mod)

REPO = Path(__file__).resolve().parents[1]
CONFIGS = REPO / "evals" / "configs"
RESULTS = REPO / "evals" / "results"
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


def cmd_index(args) -> int:
    """Rebuild the scenario repo's documentation, or fail if it has drifted.

    The one command that writes to the scenario repo. It writes on `master`,
    which no run ever materializes, so the pages can repeat what a run
    withholds.
    """
    repo = _scenario_repo(args)
    results = Path(args.results)
    if args.check:
        stale = catalog_mod.check(repo, results)
        if not stale:
            print("Scenario repo docs are up to date.")
            return 0
        sys.exit(f"Stale: {', '.join(stale)}. Run: python -m evals index")
    for path in catalog_mod.write(repo, results):
        print(f"Wrote {path}")
    return 0


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
    configs = [agent_config.load(CONFIGS / f"{name}.yaml")
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

    # One checkout per configuration, held open for the whole batch and removed
    # when it ends. Per-run checkouts would be correct too and cost a `git
    # worktree add` per run; per-batch is the same isolation for one add per
    # arm, because a run never writes to the tree it is launched from.
    with contextlib.ExitStack() as trees:
        for config in configs:
            trees.enter_context(config.checkout())

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

    # Two columns, not one. `solved` counts `verified` -- every fail_to_pass
    # flipped and every pass_to_pass held -- and `weak` counts the runs that got
    # there by weakening something they were told not to touch. They do not
    # share a scale, so nothing here adds them up.
    print(f"{'config':<20}{'runs':>6}{'traced':>8}{'solved':>8}{'rate':>8}"
          f"{'weak':>6}{'ext':>5}{'calls':>8}{'bounces':>9}  failure classes")
    for name, group in sorted(grouped.items()):
        solved = sum(1 for r in group if r.get("verified"))
        # `extended_files` is absent from anything the previous integrity
        # oracle scored, so its absence says which evaluator produced the
        # verdict. Those runs counted an added test as tampering; leaving them
        # in the weak column would carry that mistake forward.
        current = [r for r in group if "extended_files" in r]
        weak = sum(1 for r in current if r.get("tampered_files"))
        extended = sum(1 for r in current if r.get("extended_files"))
        classes = defaultdict(int)
        for row in group:
            if row.get("failure_class"):
                classes[row["failure_class"]] += 1
        summary = ", ".join(f"{k}={v}" for k, v in sorted(classes.items())) or "-"
        # `calls` and `bounces` are means over the traced runs only, so the
        # two counts have to be visible side by side: a version whose trace
        # never arrived reports zero cost, not no cost.
        print(f"{name:<20}{len(group):>6}"
              f"{len(catalog_mod._traced(group)):>8}"
              f"{solved:>8}{solved / len(group):>8.0%}"
              f"{weak:>6}{extended:>5}"
              f"{catalog_mod._mean(group, 'provider_calls'):>8.1f}"
              f"{catalog_mod._mean(group, 'failover_bounces'):>9.1f}  {summary}")
    return 0


def cmd_bundle(args) -> int:
    rows = run_mod.load_records(Path(args.results))
    if args.config:
        rows = [r for r in rows if r["config"] in args.config]
    if args.since:
        # The run id leads with its stamp, so this is a prefix comparison.
        rows = [r for r in rows if r["run_id"][:len(args.since)] >= args.since]
    if not rows:
        sys.exit("No matching runs.")
    text = bundle_mod.build(Path(args.results), rows)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"{len(rows)} run(s) -> {args.out}")
    else:
        print(text)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="evals")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="check scenarios are well-formed")
    validate.add_argument("--scenarios", default="")
    validate.add_argument("--topic", default="")
    validate.add_argument("--scenario", help="a full tag, e.g. scenario/<topic>/<id>")
    validate.set_defaults(func=cmd_validate)

    index = sub.add_parser("index", help="rebuild the scenario repo's docs")
    index.add_argument("--scenarios", default="")
    index.add_argument("--results", default=str(RESULTS))
    index.add_argument("--check", action="store_true",
                       help="fail if the docs have drifted from tags or runs")
    index.set_defaults(func=cmd_index)

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

    # The J2 analyst reads a batch, not a run. Assembling the evidence here
    # keeps the skill's context spent on analysis rather than on globbing.
    bundle = sub.add_parser("bundle", help="collect a batch's evidence for analysis")
    bundle.add_argument("--config", action="append")
    bundle.add_argument("--since", default="", help="UTC stamp, e.g. 20260806T000000Z")
    bundle.add_argument("--results", default=str(RESULTS))
    bundle.add_argument("--out", default="", help="write here instead of stdout")
    bundle.set_defaults(func=cmd_bundle)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
