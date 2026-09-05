"""The scenario repo's `master`: what the eval set holds, and what it scored.

Two generated pages, both for a human and neither read by any run.

**`docs/scenarios/`** — the set. The runner reads scenarios one tag at a time
and never needs an overview; a person deciding what to author next does, and
the only overview that existed was a table hand-maintained in that repo's
README which was already wrong — `topic/pipeline` had been a branch with no
scenarios and no row in it. A hand-kept index of a growing set decays silently,
and an index nobody trusts is worse than none. So it is generated from the
tags, which are what runs actually cite, and `--check` fails on drift.

**`docs/results/`** — what each agent version scored. `evals/results/runs/` is
gitignored and lives only on the machine that produced it
([10.6](../docs/10-metrics.md)), so a comparison currently survives only as
prose someone remembered to write. This page is the durable ledger. It belongs
here rather than in the harness repo for the reason
[8.3](../docs/08-evaluation-method.md) gives for keeping results out of it:
configurations are *branches* of that repo, so a shared index would conflict on
every merge. The scenario repo has one branch and no forks, so it does not.

`master` is never materialized into a workdir, so both pages can safely repeat
what a run withholds. What keeps that safe is that no *scenario* commit carries
these paths, which `verify.validate` asserts — see the note there.
"""

import math
from collections import defaultdict
from pathlib import Path

from evals import run as run_mod, scenario as scenario_mod

# The categories and levels the set is meant to cover, so the gap list is a
# statement about coverage rather than a list of what happens to exist.
# Mirrors docs/09-scenarios.md; `generative` is the addition this branch makes.
CATEGORIES = ("bugfix", "feature", "generative", "tests", "refactor",
              "long-context", "ambiguous", "trap")
LEVELS = ("L0", "L1", "L2", "L3")

SCENARIOS_INDEX = "docs/scenarios/README.md"
RESULTS_INDEX = "docs/results/README.md"

# Neither page may ever appear inside a scenario commit: `docs/` is visible
# seed content in three scenarios today, so a topic branch rooted on a master
# commit carrying these would hand a human's full explanation to the agent.
PAGES = (SCENARIOS_INDEX, RESULTS_INDEX)

HEADER = """# Scenario catalogue

*Generated — `python -m evals index` in the harness repo rewrites this file
from the scenario tags. Don't edit it by hand; `python -m evals index --check`
fails when it has drifted, which is how a scenario added without a rebuild
gets caught.*

Every row is a tag, because a tag is what a run cites and tags are never
moved. The prose under each one is its `evaluation/scenario.md`, withheld from
the agent during a run and repeated here because this branch is not.
"""


def topic_branches(repo: Path) -> list:
    """Every `topic/*` branch, tagged or not.

    Derived from the branches rather than from the tags, because a topic
    branch carrying no scenario is exactly the gap worth seeing.
    """
    out = scenario_mod._git(repo, "for-each-ref", "--format=%(refname:strip=2)",
                            "refs/heads/topic")
    return sorted(out.split())


def branches_for(repo: Path, tag: str) -> list:
    """The topic branches a scenario commit is reachable from.

    `%(objectname)` on an annotated tag is the tag object, not the commit, so
    resolve through `^{commit}` before asking what contains it.
    """
    out = scenario_mod._git(repo, "for-each-ref", "--format=%(refname:strip=2)",
                            f"--contains={tag}^{{commit}}", "refs/heads/")
    return sorted(out.split())


def _probe(sections: dict, scenario) -> str:
    """One line for the table: the first line of the challenge, or the title."""
    challenge = sections.get("the challenge", "")
    first = next((line.strip() for line in challenge.splitlines() if line.strip()), "")
    return first or scenario.title or "—"


def collect(repo: Path) -> list:
    """(scenario, sections, branches) per tag, in tag order.

    A malformed scenario is reported, not raised past: an index that refuses
    to build because one tag is broken tells you less than one that builds and
    names it.
    """
    rows = []
    for tag in scenario_mod.list_scenarios(repo):
        try:
            scenario = scenario_mod.load(repo, tag)
        except Exception as exc:                       # noqa: BLE001 - report it
            rows.append((tag, None, {}, [], str(exc)))
            continue
        rows.append((tag, scenario, scenario_mod.describe(repo, tag),
                     branches_for(repo, tag), ""))
    return rows


def _gaps(repo: Path, rows: list) -> list:
    """What the set does not cover yet. The reason the catalogue exists."""
    ok = [r for r in rows if r[1] is not None]
    seen_categories = {r[1].category for r in ok}
    seen_levels = {r[1].difficulty for r in ok}
    tagged = {b for r in ok for b in r[3]}

    gaps = []
    missing = [c for c in CATEGORIES if c not in seen_categories]
    if missing:
        gaps.append(f"**Categories with no scenario:** {', '.join(missing)}")
    missing = [level for level in LEVELS if level not in seen_levels]
    if missing:
        gaps.append(f"**Levels with no scenario:** {', '.join(missing)}")
    empty = [b for b in topic_branches(repo) if b not in tagged]
    if empty:
        gaps.append(f"**Topic branches carrying no scenario tag:** {', '.join(empty)}")
    undocumented = [r[0] for r in ok if not r[2]]
    if undocumented:
        gaps.append("**Scenarios with no `evaluation/scenario.md`:** "
                    + ", ".join(undocumented))
    broken = [f"`{r[0]}` ({r[4]})" for r in rows if r[1] is None]
    if broken:
        gaps.append(f"**Tags that do not load:** {'; '.join(broken)}")
    # An empty test set passes validation and then passes every run, so it is
    # worth shouting about here as well as in `validate`.
    silent = [r[0] for r in ok if not r[1].fail_to_pass]
    if silent:
        gaps.append("**Scenarios with an empty `fail_to_pass` — these pass every "
                    "run without measuring anything:** " + ", ".join(silent))
    return gaps


def render(repo: Path) -> str:
    rows = collect(repo)
    ok = [r for r in rows if r[1] is not None]
    topics = sorted({r[1].topic for r in ok})

    out = [HEADER, "",
           f"**{len(ok)} scenarios across {len(topics)} topics.**", "",
           "## The set", "",
           "| Scenario | Branch | Category | Level | Tasks | f2p / p2p | Probes |",
           "| --- | --- | --- | --- | --- | --- | --- |"]
    for tag, scenario, sections, branches, error in rows:
        if scenario is None:
            out.append(f"| `{tag}` | — | **malformed** | — | — | — | {error} |")
            continue
        tasks = ", ".join(f"`{t.id}`" for t in scenario.tasks) or "—"
        out.append(
            f"| [`{tag}`](#{_anchor(tag)}) | {', '.join(branches) or '(unreferenced)'} "
            f"| {scenario.category} | {scenario.difficulty} | {tasks} "
            f"| {len(scenario.fail_to_pass)} / {len(scenario.pass_to_pass)} "
            f"| {_probe(sections, scenario)} |")

    gaps = _gaps(repo, rows)
    if gaps:
        out += ["", "## Gaps", "",
                "What the set does not cover. This is the section to read "
                "before authoring.", ""]
        out += [f"- {gap}" for gap in gaps]

    out += ["", "## Scenarios", ""]
    for tag, scenario, sections, branches, _ in rows:
        if scenario is None:
            continue
        out += [f"### {tag}", "",
                f"**{scenario.title}**", "",
                f"*{scenario.category} · {scenario.difficulty} · "
                f"{', '.join(scenario.tags) or 'no tags'} · "
                f"branch {', '.join(branches) or '(unreferenced)'} · "
                f"timeout {scenario.timeout_s}s*", ""]
        for heading, body in sections.items():
            out += [f"**{heading.capitalize()}**", "", body, ""]
        if not sections:
            out += ["*No `evaluation/scenario.md`. What this scenario probes is "
                    "recorded only in its withheld `criteria.md`.*", ""]
        out += ["Tasks:", ""]
        for task in scenario.tasks:
            prompt = " ".join(task.prompt.split())
            out.append(f"- **`{task.id}`** — suites {task.suite or '[]'}. "
                       f"{prompt[:200]}{'…' if len(prompt) > 200 else ''}")
        out += ["",
                f"Verified by {len(scenario.fail_to_pass)} `fail_to_pass` and "
                f"{len(scenario.pass_to_pass)} `pass_to_pass` tests"
                + (f"; immutable: {', '.join(f'`{f}`' for f in scenario.immutable)}"
                   if scenario.immutable else "") + ".", ""]
    return "\n".join(out).rstrip() + "\n"


def _anchor(tag: str) -> str:
    return tag.replace("/", "").replace(".", "")


# --- results -------------------------------------------------------------

RESULTS_HEADER = """# Results

*Generated — `python -m evals index` in the harness repo rewrites this file
from the run records. Don't edit it by hand.*

`evals/results/runs/` is gitignored and lives only on the machine that produced
it, so without this page a comparison survives only as prose someone remembered
to write. Here is the ledger; the raw evidence — diffs, hidden-test output,
traces — stays local, and each row names the `run_id` that holds it.

**An agent version is a configuration at a commit.** `ref: master` today and
`ref: master` next week are different versions and are listed separately,
because that is what the run records say.
"""


def _wilson(passed: int, total: int, z: float = 1.96) -> tuple:
    """95% interval on a pass rate.

    [8.6](../docs/08-evaluation-method.md) requires it and treats overlapping
    intervals as no difference; `show` prints a bare rate, which is how
    [11.3](../docs/11-eval-status.md)'s "the pass column is noise" ended up in
    a footnote instead of in the output.
    """
    if not total:
        return (0.0, 0.0)
    p = passed / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    spread = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return (max(0.0, centre - spread), min(1.0, centre + spread))


def _mean(rows: list, key: str) -> float:
    values = [r.get(key) or 0 for r in rows]
    return sum(values) / len(values) if values else 0.0


def render_results(results_dir: Path) -> str:
    records = run_mod.load_records(results_dir) if results_dir.is_dir() else []
    out = [RESULTS_HEADER, ""]
    if not records:
        return "\n".join(out + [
            "No runs recorded on this machine yet.", ""])

    versions = defaultdict(list)
    for record in records:
        versions[(record.get("config"), (record.get("config_sha") or "")[:8])].append(record)

    out += [f"**{len(records)} runs across {len(versions)} agent versions.**", "",
            "## Agent versions", "",
            "| Configuration | commit | runs | pass | 95% interval | `tokens_in` mean "
            "| calls | bounces | failure classes |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for (name, sha), group in sorted(versions.items()):
        passed = sum(1 for r in group if r.get("outcome") == "pass")
        low, high = _wilson(passed, len(group))
        classes = defaultdict(int)
        for record in group:
            if record.get("failure_class"):
                classes[record["failure_class"]] += 1
        out.append(
            f"| `{name}` | `{sha}` | {len(group)} | {passed}/{len(group)} "
            f"| {low:.0%}–{high:.0%} | {_mean(group, 'tokens_in'):,.0f} "
            f"| {_mean(group, 'provider_calls'):.1f} "
            f"| {_mean(group, 'failover_bounces'):.1f} "
            f"| {', '.join(f'{k}={v}' for k, v in sorted(classes.items())) or '—'} |")

    out += ["",
            "Intervals this wide do not order anything. Two versions whose "
            "intervals overlap are *not* ranked by the pass column — read "
            "`tokens_in` and the failure classes instead, which is where the "
            "recorded differences have actually been.", ""]

    by_scenario = defaultdict(list)
    for record in records:
        by_scenario[(record.get("scenario"), record.get("task"))].append(record)

    out += ["## By scenario", "",
            "Which scenarios still separate one version from another. A row "
            "every version passes, or every version fails, carries no "
            "information.", "",
            "| Scenario / task | runs | pass | versions | `tokens_in` mean |",
            "| --- | --- | --- | --- | --- |"]
    for (scenario, task), group in sorted(by_scenario.items()):
        passed = sum(1 for r in group if r.get("outcome") == "pass")
        seen = len({(r.get("config"), (r.get("config_sha") or "")[:8]) for r in group})
        out.append(f"| `{scenario}` / `{task}` | {len(group)} | {passed}/{len(group)} "
                   f"| {seen} | {_mean(group, 'tokens_in'):,.0f} |")

    out += ["", "## Every run", "",
            "Chronological. The evidence for each is in "
            "`evals/results/runs/<run_id>/` on the machine named by the batch.", "",
            "| `run_id` | scenario / task | version | outcome | class | `tokens_in` |",
            "| --- | --- | --- | --- | --- | --- |"]
    for record in sorted(records, key=lambda r: r.get("run_id", "")):
        out.append(
            f"| `{record.get('run_id')}` | `{record.get('scenario')}` / "
            f"`{record.get('task')}` | `{record.get('config')}` @ "
            f"`{(record.get('config_sha') or '')[:8]}` | {record.get('outcome')} "
            f"| {record.get('failure_class') or '—'} "
            f"| {record.get('tokens_in') or 0:,} |")
    return "\n".join(out).rstrip() + "\n"


# --- writing -------------------------------------------------------------

def _pages(repo: Path, results_dir: Path) -> dict:
    return {SCENARIOS_INDEX: render(repo),
            RESULTS_INDEX: render_results(results_dir)}


def write(repo: Path, results_dir: Path) -> list:
    written = []
    for relative, text in _pages(repo, results_dir).items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def check(repo: Path, results_dir: Path) -> list:
    """The pages that have drifted. Empty when master is up to date."""
    stale = []
    for relative, text in _pages(repo, results_dir).items():
        path = repo / relative
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            stale.append(relative)
    return stale
