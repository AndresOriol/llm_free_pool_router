"""The scenario repo's `master`: what the eval set holds, and what it scored.

Generated documentation, all of it for a human and none of it read by any run.

**`docs/scenarios/`** — the set, as a small wiki: an index, and one page per
scenario at `<topic>/<id>.md`. The runner reads scenarios one tag at a time and
never needs an overview; a person deciding what to author next does, and the
only overview that existed was a table hand-maintained in that repo's README
which was already wrong — `topic/pipeline` had been a branch with no scenarios
and no row in it. A hand-kept index of a growing set decays silently, and an
index nobody trusts is worse than none. So it is generated from the tags, which
are what runs actually cite, and `--check` fails on drift.

One page per scenario rather than one page for all of them because the unit a
reader works in is a scenario: the page a tag's `evaluation/scenario.md` fills
runs to a couple of hundred lines, and ten of them concatenated is a document
nobody opens twice. Split, each is a stable address a result or a commit
message can cite, and a backticked scenario id in the prose becomes a link.

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

SCENARIOS_DIR = "docs/scenarios"
RESULTS_DIR = "docs/results"
SCENARIOS_INDEX = f"{SCENARIOS_DIR}/README.md"
RESULTS_INDEX = f"{RESULTS_DIR}/README.md"

# No generated page may ever appear inside a scenario commit: `docs/` is
# visible seed content in three scenarios today, so a topic branch rooted on a
# master commit carrying one would hand a human's full explanation to the
# agent. The catalogue is a directory of pages rather than one file, so the
# check is on the directories -- a single scenario's page leaks that
# scenario's answer whole, which is worse than leaking the index.
PAGE_ROOTS = (SCENARIOS_DIR, RESULTS_DIR)


def leaked_pages(seed: Path) -> list:
    """Generated documentation found inside a materialized code state."""
    found = []
    for root in PAGE_ROOTS:
        directory = seed / root
        if directory.is_dir():
            found += [str(path.relative_to(seed)).replace("\\", "/")
                      for path in sorted(directory.rglob("*")) if path.is_file()]
    return found


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


INDEX_HEADER = """# Scenario catalogue

*Generated — `python -m evals index` in the harness repo rewrites this
directory from the scenario tags: this index, and one page per scenario under
`<topic>/<id>.md`. Don't edit them by hand; `python -m evals index --check`
fails when they have drifted, which is how a scenario added without a rebuild
gets caught.*

Every page is a tag, because a tag is what a run cites and tags are never
moved. The prose on it is its `evaluation/scenario.md`, withheld from the agent
during a run and repeated here because this branch is never materialized into a
workdir. Start from the table, or walk a topic line from its first scenario to
its last.
"""


def page_path(tag: str) -> str:
    """Where a scenario's own page lives, mirroring the tag that names it."""
    _, topic, scenario_id = tag.split("/", 2)
    return f"{SCENARIOS_DIR}/{topic}/{scenario_id}.md"


def topic_line(repo: Path, topic: str, tags: list) -> list:
    """`tags`, ordered oldest commit first.

    Each commit on a topic branch is the previous scenario's code with its fix
    applied, so branch order is the order a reader should walk them in. Tag
    order is alphabetical and says nothing about which came first.
    """
    try:
        commits = scenario_mod._git(repo, "log", "--format=%H",
                                    f"topic/{topic}").split()
    except RuntimeError:
        return tags                      # no branch to order against
    rank = {commit: position for position, commit in enumerate(commits)}

    def age(tag: str) -> int:
        try:
            commit = scenario_mod._git(repo, "rev-parse", f"{tag}^{{commit}}")
        except RuntimeError:
            return 0
        return -rank.get(commit.strip(), 0)

    return sorted(tags, key=age)


def _link(source_tag: str, target_tag: str) -> str:
    """Href from one scenario page to another, relative to its own directory."""
    target = page_path(target_tag)
    if source_tag.split("/")[1] == target_tag.split("/")[1]:
        return target.rsplit("/", 1)[1]
    return "../" + target.split("/", 2)[2]


def _crosslink(text: str, source_tag: str, by_id: dict) -> str:
    """A backticked scenario id in the prose becomes a link to its page.

    Authors already refer to a sibling that way — "the same exception
    `duration-notes` takes" — and in a single page that was as far as it went.
    Split across pages it is the whole point of splitting them.
    """
    for scenario_id, tag in by_id.items():
        if tag == source_tag:
            continue
        text = text.replace(f"`{scenario_id}`",
                            f"[`{scenario_id}`]({_link(source_tag, tag)})")
    return text


def render_scenario(scenario, sections: dict, branches: list, line: list,
                    by_id: dict) -> str:
    """One scenario's page: its metadata, its prose, its tasks, its tests."""
    tag = scenario.tag
    position = line.index(tag) + 1 if tag in line else 1
    nav = ["[← All scenarios](../README.md)"]
    if len(line) < 2:
        nav.append(f"the only scenario on **`topic/{scenario.topic}`**")
    else:
        nav.append(f"scenario {position} of {len(line)} on "
                   f"**`topic/{scenario.topic}`**")
        if position > 1:
            previous = line[position - 2]
            nav.append(f"previous: [`{previous.split('/')[2]}`]"
                       f"({_link(tag, previous)})")
        if position < len(line):
            following = line[position]
            nav.append(f"next: [`{following.split('/')[2]}`]"
                       f"({_link(tag, following)})")

    out = [f"# {tag.split('/')[2]}", "",
           f"**{scenario.title}**", "",
           f"> {_crosslink(_probe(sections, scenario), tag, by_id)}", "",
           " · ".join(nav), "",
           "| | |", "| --- | --- |",
           f"| Tag | `{tag}` |",
           f"| Branch | {', '.join(f'`{b}`' for b in branches) or '(unreferenced)'} |",
           f"| Category | [{scenario.category}](../README.md#by-category) |",
           f"| Level | [{scenario.difficulty}](../README.md#by-level) |",
           f"| Tags | {', '.join(f'`{t}`' for t in scenario.tags) or '—'} |",
           f"| Context mode | `{scenario.context_mode}` |",
           f"| Timeout | {scenario.timeout_s} s |",
           f"| Tests | {len(scenario.fail_to_pass)} `fail_to_pass` / "
           f"{len(scenario.pass_to_pass)} `pass_to_pass` |",
           f"| Tasks | {', '.join(f'`{t.id}`' for t in scenario.tasks) or '—'} |",
           ""]

    for heading, body in sections.items():
        out += [f"## {heading.capitalize()}", "",
                _crosslink(body, tag, by_id), ""]
    if not sections:
        out += ["*No `evaluation/scenario.md`. What this scenario probes is "
                "recorded only in its withheld `criteria.md`.*", ""]

    out += ["## Tasks", "",
            "A run poses one of these against the seed. Everything else on "
            "this page is withheld from the agent.", ""]
    for task in scenario.tasks:
        suites = ", ".join(f"`{s}`" for s in task.suite) or "—"
        tags = ", ".join(f"`{t}`" for t in task.tags)
        out += [f"### `{task.id}`", "",
                f"*Suites: {suites}" + (f" · tags: {tags}" if tags else "") + "*",
                "",
                f"> {' '.join(task.prompt.split())}", ""]
    if not scenario.tasks:
        out += ["*None. Nothing can be posed against this scenario.*", ""]

    out += ["## Verification", "",
            "The tests below live in the withheld `evaluation/` directory, so "
            "an attempt is scored against checks it could not read.", "",
            f"**`fail_to_pass` ({len(scenario.fail_to_pass)})** — must fail "
            "against the untouched seed and pass once the task is done.", ""]
    out += [f"- `{t}`" for t in scenario.fail_to_pass] or [
        "- *none — this scenario passes every run without measuring anything.*"]
    out += ["", f"**`pass_to_pass` ({len(scenario.pass_to_pass)})** — already "
            "green, and must stay green.", ""]
    out += [f"- `{t}`" for t in scenario.pass_to_pass] or ["- *none*"]
    if scenario.immutable:
        out += ["", "**Immutable** — editing these is recorded as tampering "
                "rather than scored as a result.", ""]
        out += [f"- `{f}`" for f in scenario.immutable]

    prose = " ".join(sections.values())
    related = [t for t in by_id.values() if t != tag
               and (f"`{t.split('/')[2]}`" in prose or t in line)]
    if related:
        out += ["", "## See also", ""]
        for other in related:
            why = ("same topic line" if other in line else "referred to above")
            out.append(f"- [`{other.split('/')[2]}`]({_link(tag, other)}) — {why}")

    out += ["", "[← All scenarios](../README.md) · "
            "[what each version scored](../../results/README.md) · "
            "[how the set is laid out](../../../README.md)", "",
            "---", "",
            f"*This page is the `evaluation/scenario.md` of `{tag}`, plus its "
            "`scenario.yaml` and task files. A run never sees any of it.*", ""]
    return "\n".join(out).rstrip() + "\n"


def render(repo: Path, rows: list = None) -> str:
    """The index: the whole set once as a table, then cut three other ways.

    `rows` is the output of `collect`, passed in by `_pages` so that building
    the whole directory reads the tags once rather than once per page.
    """
    rows = collect(repo) if rows is None else rows
    ok = [r for r in rows if r[1] is not None]
    topics = sorted({r[1].topic for r in ok})
    by_id = {r[0].split("/")[2]: r[0] for r in ok}

    out = [INDEX_HEADER, "",
           f"**{len(ok)} scenarios across {len(topics)} topics.** See also: "
           "[what each agent version scored](../results/README.md) · "
           "[how the set is laid out](../../README.md).", "",
           "## The set", "",
           "| Scenario | Topic | Category | Level | Tasks | f2p / p2p | Probes |",
           "| --- | --- | --- | --- | --- | --- | --- |"]
    for tag, scenario, sections, branches, error in rows:
        if scenario is None:
            out.append(f"| `{tag}` | — | **malformed** | — | — | — | {error} |")
            continue
        tasks = ", ".join(f"`{t.id}`" for t in scenario.tasks) or "—"
        page = page_path(tag).split("/", 2)[2]
        out.append(
            f"| [`{tag.split('/')[2]}`]({page}) | `topic/{scenario.topic}` "
            f"| {scenario.category} | {scenario.difficulty} | {tasks} "
            f"| {len(scenario.fail_to_pass)} / {len(scenario.pass_to_pass)} "
            f"| {_probe(sections, scenario)} |")

    def listing(matches: list) -> str:
        return ", ".join(f"[`{r[0].split('/')[2]}`]"
                         f"({page_path(r[0]).split('/', 2)[2]})"
                         for r in matches) or "*nothing yet*"

    out += ["", "## By category", "",
            "The eight categories the set is meant to cover. An empty one is a "
            "gap, not a category that does not apply.", ""]
    for category in CATEGORIES:
        out.append(f"- **{category}** — "
                   + listing([r for r in ok if r[1].category == category]))

    out += ["", "## By level", "",
            "L0 checks the harness rather than the agent; L3 is a scenario a "
            "strong session still loses.", ""]
    for level in LEVELS:
        out.append(f"- **{level}** — "
                   + listing([r for r in ok if r[1].difficulty == level]))

    out += ["", "## Topic lines", "",
            "Each commit on a topic branch is a scenario, and each one is the "
            "previous scenario's code with its fix applied — so a line with "
            "more than one entry reads as a codebase evolving rather than as "
            "unrelated trees. Oldest first.", ""]
    for topic in topics:
        tags = topic_line(repo, topic, [r[0] for r in ok if r[1].topic == topic])
        out += [f"### `topic/{topic}`", ""]
        for position, tag in enumerate(tags, 1):
            scenario = next(r[1] for r in ok if r[0] == tag)
            out.append(f"{position}. [`{tag.split('/')[2]}`]"
                       f"({page_path(tag).split('/', 2)[2]}) — {scenario.title}")
        out.append("")

    gaps = _gaps(repo, rows)
    out += ["## Gaps", ""]
    if gaps:
        out += ["What the set does not cover. This is the section to read "
                "before authoring.", ""]
        out += [f"- {gap}" for gap in gaps]
    else:
        out += ["Every category and level has at least one scenario, every "
                "topic branch carries a tag, every scenario has a page and a "
                "non-empty `fail_to_pass`. Adding a scenario means deepening a "
                "category, not filling a hole.", ""]
    return "\n".join(out).rstrip() + "\n"


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


def _solved(rows: list) -> int:
    """Runs that did the work: every fail_to_pass flipped, every pass_to_pass held.

    Not `outcome == "pass"`. An outcome is one label over two questions that do
    not share a scale -- *did it solve the task* and *did it respect what it was
    told not to touch* -- and collapsing them loses both. The first full-set
    batch recorded 3/9 while seven of the nine left every hidden test green,
    because an integrity verdict outranks everything else in `_outcome` and then
    reads as a failed task in the pass column. `verified` was in every one of
    those records already; nothing rendered it.
    """
    return sum(1 for r in rows if r.get("verified"))


# A record scored before the integrity oracle changed has no `extended_files`
# key at all, which makes the field's *absence* an exact marker for which
# evaluator produced the verdict -- no date heuristic, no version guess. Those
# records called a run `tampered` for appending a test, so their integrity
# column is not comparable to a current one and is reported as its own thing
# rather than summed with it. `config_sha` pins the agent; nothing yet pins the
# harness, which is why this has to be inferred at all.
def _scored_by_the_old_oracle(record: dict) -> bool:
    return "extended_files" not in record


def _integrity(rows: list) -> str:
    """The other question, in its own cell: weakened, and strengthened."""
    current = [r for r in rows if not _scored_by_the_old_oracle(r)]
    stale = sum(1 for r in rows if _scored_by_the_old_oracle(r) and r.get("tampered_files"))
    weakened = sum(1 for r in current if r.get("tampered_files"))
    extended = sum(1 for r in current if r.get("extended_files"))
    parts = []
    if weakened:
        parts.append(f"**{weakened} weakened**")
    if extended:
        parts.append(f"{extended} extended")
    if stale:
        parts.append(f"{stale} flagged by the previous oracle")
    return ", ".join(parts) or "clean"


def _traced(rows: list) -> list:
    """The runs that actually recorded a trace.

    A run whose `trace.jsonl` never arrived records zero for every metric
    summed over it, and a zero is indistinguishable from a measurement. Five
    `deepagents` runs are in the record with no trace at all -- written before
    the trace path was made absolute, so the agent wrote it inside its own
    worktree -- and they pull that version's `tokens_in` mean to exactly 0,
    which the table then prints beside a real one. `provider_calls` is the
    marker: a run that reached a model made at least one.
    """
    return [r for r in rows if r.get("provider_calls")]


def _retired(rows: list) -> str:
    """Members that went away mid-run, named next to the bounces they caused.

    A retirement is the router working: the member is dropped and the run
    carries on. It costs no tokens and fails nothing, so no metric anyone reads
    moves — which is how one dead model came to own 55% of a batch's bounces
    without being noticed. Naming it here is the whole fix.
    """
    gone = sorted({m for r in rows for m in (r.get("retired_models") or [])})
    return f" — retired: {', '.join(f'`{m}`' for m in gone)}" if gone else ""


def _account(rows: list) -> str:
    """How often the run left an account, over the runs that could leave one.

    `wrote_account` is None where the seed ships no feedback file, and those are
    excluded rather than counted as failures -- otherwise the number improves
    every time a scenario is added that does not test this.
    """
    applicable = [r for r in rows if r.get("wrote_account") is not None]
    if not applicable:
        return "—"
    wrote = sum(1 for r in applicable if r["wrote_account"])
    return f"{wrote}/{len(applicable)}"


def _mean(rows: list, key: str) -> float:
    """Averaged over the traced runs only, so a 0 means measured-as-zero."""
    values = [r.get(key) or 0 for r in _traced(rows)]
    return sum(values) / len(values) if values else 0.0


def _unmeasured(rows: list) -> str:
    missing = len(rows) - len(_traced(rows))
    return f" ({missing} untraced)" if missing else ""


def _scenario_link(scenario_id: str, pages: dict) -> str:
    """A results row's scenario id, linked to its page in the catalogue.

    `pages` maps id to page path; a run citing a scenario that no longer has a
    tag stays plain text rather than becoming a dead link.
    """
    page = pages.get(scenario_id)
    if not page:
        return f"`{scenario_id}`"
    return f"[`{scenario_id}`](../{page.split('/', 1)[1]})"


def _run_integrity(record: dict) -> str:
    """One run's integrity cell: what it weakened, and what it strengthened."""
    flagged = record.get("tampered_files") or []
    if _scored_by_the_old_oracle(record):
        return ", ".join(f"`{path}` (previous oracle)" for path in flagged) or "—"
    parts = [f"**weakened** `{path}`" for path in flagged]
    parts += [f"extended `{path}`" for path in record.get("extended_files") or []]
    return ", ".join(parts) or "—"


def render_results(results_dir: Path, pages: dict = None) -> str:
    pages = pages or {}
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
            "| Configuration | commit | runs | solved | 95% interval | integrity "
            "| `tokens_in` mean | tok/call | calls | bounces | account "
            "| +tests | failure classes |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- "
            "| --- | --- |"]
    for (name, sha), group in sorted(versions.items()):
        passed = _solved(group)
        low, high = _wilson(passed, len(group))
        classes = defaultdict(int)
        for record in group:
            if record.get("failure_class"):
                classes[record["failure_class"]] += 1
        out.append(
            f"| `{name}` | `{sha}` | {len(group)} | {passed}/{len(group)} "
            f"| {low:.0%}–{high:.0%} | {_integrity(group)} "
            f"| {_mean(group, 'tokens_in'):,.0f}{_unmeasured(group)} "
            f"| {_mean(group, 'tokens_per_call'):,.0f} "
            f"| {_mean(group, 'provider_calls'):.1f} "
            f"| {_mean(group, 'failover_bounces'):.1f}{_retired(group)} "
            f"| {_account(group)} "
            f"| {sum(r.get('added_tests') or 0 for r in group)} "
            f"| {', '.join(f'{k}={v}' for k, v in sorted(classes.items())) or '—'} |")

    out += ["",
            "**solved** is `verified`: every `fail_to_pass` test flipped and "
            "every `pass_to_pass` test still passing. **integrity** is the "
            "separate question of whether the run respected what it was told "
            "not to touch, and it is deliberately not a point on the same "
            "scale — a version that solves nothing and a version that solves "
            "everything by rewriting the tests are both bad, in ways no single "
            "rate can hold. A weakening still makes the run's `outcome` "
            "`tampered`; this table refuses to average that into a pass rate.",
            "",
            "Runs recorded before the integrity oracle changed are marked *by "
            "the previous oracle*: that oracle hashed the file, so it could not "
            "tell appending a regression test from deleting an assertion and "
            "called both tampering. Their integrity verdicts are not comparable "
            "with the ones below them, and are not counted with them.",
            "",
            "**account** is how often the run appended to the project's "
            "`NOTES.md`, over the runs whose scenario ships one. "
            "[R7](../../docs/design/long-run-harness.md) calls that file \"the "
            "whole human interface\", and it is the only part of an account "
            "that can be checked without reading it. Nothing gates on this "
            "yet — it is here to establish a baseline. **+tests** is test "
            "functions the runs added that nothing asked for, which until "
            "recently the harness could only score as tampering. A **bounces** "
            "cell naming a *retired* model is the pool dropping a member that "
            "has gone away upstream: correct behaviour, free, and invisible "
            "everywhere else — delete it from the config.",
            "",
            "A mean marked *untraced* was taken over fewer runs than the row "
            "counts. A run whose `trace.jsonl` never arrived records zero for "
            "everything summed over it, and averaging that in reports a cost "
            "of nothing as though it had been measured.",
            "",
            "Intervals this wide do not order anything. Two versions whose "
            "intervals overlap are *not* ranked by the solved column — read "
            "`tokens_in` and the failure classes instead, which is where the "
            "recorded differences have actually been.", ""]

    by_scenario = defaultdict(list)
    for record in records:
        by_scenario[(record.get("scenario"), record.get("task"))].append(record)

    out += ["## By scenario", "",
            "Which scenarios still separate one version from another. A row "
            "every version passes, or every version fails, carries no "
            "information.", "",
            "| Scenario / task | runs | solved | integrity | versions "
            "| `tokens_in` mean |",
            "| --- | --- | --- | --- | --- | --- |"]
    for (scenario, task), group in sorted(by_scenario.items()):
        passed = _solved(group)
        seen = len({(r.get("config"), (r.get("config_sha") or "")[:8]) for r in group})
        out.append(f"| {_scenario_link(scenario, pages)} / `{task}` | {len(group)} "
                   f"| {passed}/{len(group)} | {_integrity(group)} | {seen} "
                   f"| {_mean(group, 'tokens_in'):,.0f} |")

    out += ["", "## Every run", "",
            "Chronological. The evidence for each is in "
            "`evals/results/runs/<run_id>/` on the machine named by the batch.", "",
            "| `run_id` | scenario / task | version | solved | outcome "
            "| integrity | class | `tokens_in` |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for record in sorted(records, key=lambda r: r.get("run_id", "")):
        out.append(
            f"| `{record.get('run_id')}` "
            f"| {_scenario_link(record.get('scenario'), pages)} / "
            f"`{record.get('task')}` | `{record.get('config')}` @ "
            f"`{(record.get('config_sha') or '')[:8]}` "
            f"| {'yes' if record.get('verified') else 'no'} "
            f"| {record.get('outcome')} "
            f"| {_run_integrity(record)} "
            f"| {record.get('failure_class') or '—'} "
            f"| {record.get('tokens_in') or 0:,} |")
    return "\n".join(out).rstrip() + "\n"


# --- writing -------------------------------------------------------------

def _pages(repo: Path, results_dir: Path) -> dict:
    """Every generated page: the two indexes, and one page per scenario."""
    rows = collect(repo)
    by_id = {r[0].split("/")[2]: r[0] for r in rows if r[1] is not None}
    lines = {}
    scenario_pages = {tag.split("/")[2]: page_path(tag)
                      for tag, scenario, *_ in rows if scenario is not None}
    pages = {SCENARIOS_INDEX: render(repo, rows),
             RESULTS_INDEX: render_results(results_dir, scenario_pages)}
    for tag, scenario, sections, branches, _ in rows:
        if scenario is None:
            continue                     # named in the index; has nothing to say
        if scenario.topic not in lines:
            lines[scenario.topic] = topic_line(
                repo, scenario.topic,
                [r[0] for r in rows
                 if r[1] is not None and r[1].topic == scenario.topic])
        pages[page_path(tag)] = render_scenario(
            scenario, sections, branches, lines[scenario.topic], by_id)
    return pages


def _orphans(repo: Path, pages: dict) -> list:
    """Pages under the catalogue that no scenario claims any more.

    A moved or renamed tag would otherwise leave its page sitting there being
    read, which is the failure mode a generated catalogue exists to prevent.
    """
    directory = repo / SCENARIOS_DIR
    if not directory.is_dir():
        return []
    return sorted(str(path.relative_to(repo)).replace("\\", "/")
                  for path in directory.rglob("*.md")
                  if str(path.relative_to(repo)).replace("\\", "/") not in pages)


def write(repo: Path, results_dir: Path) -> list:
    written = []
    pages = _pages(repo, results_dir)
    for relative, text in pages.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        # Explicit LF: the scenario repo is read on more than one platform,
        # and a page rewritten with CRLF is a whole-file diff saying nothing.
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
    for relative in _orphans(repo, pages):
        (repo / relative).unlink()
        print(f"Removed {relative} (no scenario claims it)")
    for topic in sorted((repo / SCENARIOS_DIR).glob("*")):
        if topic.is_dir() and not any(topic.iterdir()):
            topic.rmdir()
    return written


def check(repo: Path, results_dir: Path) -> list:
    """The pages that have drifted. Empty when master is up to date."""
    pages = _pages(repo, results_dir)
    stale = []
    for relative, text in pages.items():
        path = repo / relative
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            stale.append(relative)
    return stale + _orphans(repo, pages)
