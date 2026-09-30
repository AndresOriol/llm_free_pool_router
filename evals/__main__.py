"""CLI: python -m evals <command>

    validate [--topic T] [--scenario TAG]     the untouched/gold-patch gate
    index [--check]                           rebuild the scenario repo docs
    run --config NAME [...]                   execute runs and record them
    show [--config NAME]                      summarize recorded runs
    bundle [--config NAME] [--out FILE]       collect a batch's evidence for J2
    probes [--dataset D] [--list] [--push] [--experiment] [--stale]
           [--from-run RECORD --turn N --agent A]
                                             one agent, one situation, one
                                             decision -- the small tests
    mine --out DIR [--sessions DIR]           recorded sessions -> scenario material

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
                   mine as mine_mod, run as run_mod, scenario as scenario_mod,
                   verify as verify_mod)

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
    scenarios = [scenario_mod.load(repo, tag) for tag in tags]
    split = getattr(args, "split", "")
    if split:
        if split not in ("train", "holdout"):
            sys.exit(f"Invalid split {split!r}. Expected 'train' or 'holdout'.")
        from evals import splits
        filtered = []
        for s in scenarios:
            try:
                if splits.split_of(s.id) == split:
                    filtered.append(s)
            except ValueError as exc:
                sys.exit(f"Scenario {s.id!r} is not listed in /evals/splits.yaml. Add it before running with --split.")
        scenarios = filtered
        if not scenarios:
            sys.exit(f"No scenarios matched split {split!r}.")
    return scenarios


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
    configs = [agent_config.load(CONFIGS / f"{name}.yaml", getattr(args, "ref", ""))
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

        # A run that dies takes only itself. The runner already returns a record
        # for a crashed *agent*; this is for the runner failing around it -- a
        # missing file, a git error, a disk full -- and one such failure ended a
        # batch at run 6 of 18, spending five runs of free-tier quota on no
        # comparison. Interleaving means the arms stay balanced either way.
        broken = []
        for index, (config, scenario, task, rep) in enumerate(plan, 1):
            print(f"[{index}/{len(plan)}] {scenario.topic}/{scenario.id}/{task.id} "
                  f"{config.name} rep{rep} ... ", end="", flush=True)
            try:
                record = run_mod.execute_run(repo, scenario, task, config, rep,
                                             results)
            except Exception as exc:  # noqa: BLE001 - reported, batch continues
                broken.append(f"{scenario.id}/{task.id} {config.name} rep{rep}: "
                              f"{exc!r}")
                print(f"RUNNER ERROR: {exc!r}")
                continue
            detail = record["failure_class"] or ""
            print(f"{record['outcome']}{' (' + detail + ')' if detail else ''} "
                  f"[{record['wall_time_s']}s, {record['provider_calls']} calls]")

    if broken:
        # Loud, and a non-zero exit: these runs recorded nothing, so the batch
        # is not the comparison it was asked for.
        print(f"\n{len(broken)} run(s) failed in the runner and recorded nothing:")
        for line in broken:
            print(f"  {line}")
        return 1
    return 0


def cmd_show(args) -> int:
    rows = run_mod.load_records(Path(args.results))
    if args.config:
        rows = [r for r in rows if r["config"] in args.config]
    else:
        # Named explicitly you get them; in the leaderboard you do not. A stub
        # calls no model, so 4/4 at 912 tokens would top every column it is in.
        rows = [r for r in rows if not catalog_mod.is_stub(r)]
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


def cmd_mine(args) -> int:
    """Recorded Claude Code sessions into a corpus a scenario can be drawn from."""
    sessions = Path(args.sessions or mine_mod.DEFAULT_SESSIONS)
    if not sessions.is_dir():
        sys.exit(f"No session store at {sessions}. Pass --sessions.")
    out = Path(args.out).resolve()
    index = mine_mod.mine(sessions, out)
    if not index:
        sys.exit(f"No sessions found under {sessions}.")

    by_project = defaultdict(int)
    for row in index:
        by_project[row["project"]] += 1
    print(f"{len(index)} session(s) across {len(by_project)} project(s) -> {out}")
    for name, count in sorted(by_project.items(), key=lambda kv: -kv[1]):
        print(f"  {count:4}  {name}")
    print(f"{sum(r['tool_calls'] for r in index):,} tool calls, "
          f"{sum(r['human_turns'] for r in index):,} human turns. "
          f"Read evals/ARCHETYPES.md next.")
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


def cmd_probes(args) -> int:
    """One agent, one situation, one decision — the small end of the harness."""
    from evals import probes as probes_mod

    if args.from_run:
        # A skeleton to edit, printed rather than written: which dataset it
        # belongs to and what it expects are decisions, not extraction.
        import yaml
        if not (args.turn and args.agent):
            sys.exit("--from-run needs --turn and --agent")
        skeleton = probes_mod.from_run(Path(args.from_run), args.turn,
                                       args.agent, (args.id or [""])[0])
        sys.stdout.reconfigure(encoding="utf-8")  # run records carry any text
        print(yaml.safe_dump([skeleton], allow_unicode=True, sort_keys=False,
                             width=100))
        return 0

    try:
        found = probes_mod.load(Path(args.dir) if args.dir
                                else probes_mod.PROBES_DIR)
    except ValueError as exc:
        sys.exit(str(exc))
    if args.agent:
        # `explore` includes `explore-researcher`: an agent and its sub-agents.
        found = [p for p in found if p.agent == args.agent
                 or p.agent.startswith(args.agent + "-")]
    if args.dataset:
        found = [p for p in found if p.dataset == args.dataset]
    if args.id:
        found = [p for p in found if p.id in args.id]
    if not found:
        sys.exit("No probes matched.")
    datasets = sorted({p.dataset for p in found})

    if args.list:
        for probe in found:
            print(f"{probe.dataset}  {probe.id}  ({probe.agent}, {probe.kind}, "
                  f"reviewed {probe.reviewed})")
        return 0

    if args.stale:
        due = probes_mod.stale(found)
        for probe, commits in due:
            print(f"{probe.dataset}  {probe.id}  reviewed {probe.reviewed}; "
                  f"{probe.agent} changed since:")
            for commit in commits:
                print(f"    {commit}")
        print(f"{len(due)} of {len(found)} probe(s) due for review.")
        return 1 if due else 0

    if args.push:
        from evals import probe_dataset
        for name in datasets:
            try:
                pushed = probe_dataset.push(found, name)
            except probe_dataset.NoLangSmith as exc:
                sys.exit(str(exc))
            print(f"{pushed['examples']} example(s) -> dataset {name!r} "
                  f"({pushed['created']} new, {pushed['updated']} updated, "
                  f"{pushed['deleted']} deleted)")
        if not args.run:
            return 0

    # Building the model is deferred to here so `--list` and `--push` cost no
    # provider calls and need no keys.
    from agent.utils.pool import CONTEXT_FLOOR, check_floor
    from agent.utils.chat_model import RouterChatModel
    from llm_router import AutonomousLLMRouter, load_providers_from_config

    providers = load_providers_from_config(os.environ.get("ROUTER_CONFIG") or None)
    if not providers:
        sys.exit("No providers loaded. Set your keys in llm_router/.env.")
    router = AutonomousLLMRouter(providers)
    floor = int(os.environ.get("AGENT_CONTEXT_FLOOR") or CONTEXT_FLOOR)
    members = check_floor(router, floor)
    model = RouterChatModel(router=router,
                            max_retries=len(providers) + 3).for_context(
                                floor, strict=True)

    if args.experiment:
        # A whole dataset per experiment: the failures a change targets and
        # the regressions beside them are one topic, and are judged together.
        from evals import probe_dataset
        for name in datasets:
            try:
                done = probe_dataset.evaluate(found, model, floor=floor,
                                              members=members, dataset=name,
                                              experiment=args.name or name,
                                              repetitions=args.repetitions)
            except probe_dataset.NoLangSmith as exc:
                sys.exit(str(exc))
            print(f"Experiment recorded against {name!r}: {done['experiment']}")
        return 0

    # Serial, for the reason every batch here is serial: the probes share one
    # free-tier pool, so running them at once makes each one's model mix depend
    # on the others (evals/run.py).
    results = []
    for index, probe in enumerate(found, 1):
        # One line, printed once decided: the router's failover logs land
        # while the probe runs and used to split a half-printed line.
        decision = probes_mod.first_decision(probe, model, floor=floor,
                                             members=members)
        result = probes_mod.score(probe, decision)
        results.append(result)
        print(f"[{index}/{len(found)}] {probe.id} ... "
              f"{'pass' if result['passed'] else 'FAIL'} ({result['outcome']})",
              flush=True)

    print()
    print(probes_mod.report(results))
    # Non-zero on a failure, so this is usable as a gate.
    return 0 if all(r["passed"] for r in results) else 1


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
    run.add_argument("--split", choices=["train", "holdout"], default="",
                     help="restrict scenarios to train or holdout split")
    run.add_argument("--suite", default="")
    run.add_argument("--tags", nargs="*")
    run.add_argument("--reps", type=int, default=3)
    run.add_argument("--ref", default="",
                     help="resolve every config against this ref instead of the "
                          "one it pins; how a fix on a branch gets measured")
    run.add_argument("--skip-validate", action="store_true")
    # Self-tests point this elsewhere so stub runs never land in the real
    # record -- a leaderboard mixing stubbed and measured runs is worse than
    # no leaderboard.
    run.add_argument("--results", default=str(RESULTS))
    run.set_defaults(func=cmd_run)

    mine = sub.add_parser("mine", help="recorded sessions -> scenario material")
    mine.add_argument("--out", required=True, help="directory to write the corpus into")
    mine.add_argument("--sessions", default="",
                      help=f"session store (default: {mine_mod.DEFAULT_SESSIONS})")
    mine.set_defaults(func=cmd_mine)

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

    probes = sub.add_parser(
        "probes", help="one agent, one situation, one decision")
    probes.add_argument("--agent", default="", help="code | improve | explore | explore-researcher")
    probes.add_argument("--id", nargs="*", help="only these probe ids")
    probes.add_argument("--dir", default="", help="where the yaml lives")
    probes.add_argument("--list", action="store_true",
                        help="show what would run; no provider calls")
    probes.add_argument("--push", action="store_true",
                        help="sync the probes to a LangSmith dataset")
    probes.add_argument("--run", action="store_true",
                        help="with --push, run them as well as pushing")
    probes.add_argument("--experiment", action="store_true",
                        help="run them through LangSmith, recording an "
                             "experiment against the dataset")
    probes.add_argument("--dataset", default="",
                        help="only this dataset (a probe file's topic)")
    probes.add_argument("--stale", action="store_true",
                        help="list probes whose agent changed since review")
    probes.add_argument("--from-run", default="",
                        help="a run record: print a probe skeleton from it")
    probes.add_argument("--turn", type=int, default=0,
                        help="with --from-run, the decision to stop at")
    probes.add_argument("--name", default="",
                        help="with --experiment, the experiment's name prefix")
    probes.add_argument("--repetitions", type=int, default=1,
                        help="with --experiment, runs per probe: one model "
                             "call is one sample from a pool that changes")
    probes.set_defaults(func=cmd_probes)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
