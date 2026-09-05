"""The scenario repo's generated documentation.

Nothing in `evals/` had a test before this file, which is the root cause behind
every metric defect the hardening plan lists: a broken evaluator produces
numbers that look like measurement. The catalogue is not a metric, but it is
what a person reads to decide what to author next and whether a comparison
said anything, so the same argument applies.

One named test per behaviour, after `tests/agent/test_failover.py`.
"""

import json
import subprocess
from pathlib import Path

from evals import catalog, scenario as scenario_mod

PAGE = """# demo

## The seed
One module with a bug in it.

## The task
Fix it.

## The challenge
Nothing names the file, so the whole probe is localisation.

It is hard for one further reason.

## What it checks
That the bug is gone and nothing else broke — the same exception `later` takes.
"""

SCENARIO_YAML = """id: demo
title: A demonstration scenario
category: bugfix
difficulty: L1
tags: [python]
immutable: [tests/test_demo.py]
fail_to_pass: [evaluation/tests/test_demo.py::test_fixed]
pass_to_pass: [evaluation/tests/test_demo.py::test_intact]
"""

TASK = """---
id: fix-it
suite: [smoke]
tags: [bugfix]
---

## Prompt
Fix the bug in demo.py.

## Judge notes
Not read by anything yet.
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True)


def _scenario_repo(tmp_path: Path, *, page: str = PAGE,
                   yaml_text: str = SCENARIO_YAML) -> Path:
    """A repo with one tagged scenario on one topic branch."""
    repo = tmp_path / "scenarios"
    (repo / "evaluation" / "tests").mkdir(parents=True)
    (repo / "tasks").mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    # Master carries documentation and no scenario, as in the real repo.
    (repo / "README.md").write_text("docs only\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "docs")
    (repo / "demo.py").write_text("value = 1\n", encoding="utf-8")
    (repo / "scenario.yaml").write_text(yaml_text, encoding="utf-8")
    (repo / "tasks" / "fix-it.md").write_text(TASK, encoding="utf-8")
    if page:
        (repo / "evaluation" / "scenario.md").write_text(page, encoding="utf-8")
    _git(repo, "checkout", "-q", "-b", "topic/demo")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "scenario: demo")
    _git(repo, "tag", "scenario/demo/demo")
    return repo


def _second_scenario(repo: Path) -> None:
    """A later commit on the same topic branch: the next scenario in the line.

    Named so that the alphabetical order of the tags is the reverse of the
    order the commits are in, which is the thing the topic line has to get
    right.
    """
    (repo / "demo.py").write_text("value = 2\n", encoding="utf-8")
    (repo / "scenario.yaml").write_text(
        SCENARIO_YAML.replace("id: demo", "id: later"), encoding="utf-8")
    (repo / "evaluation" / "scenario.md").write_text(
        PAGE.replace("`later`", "`demo`"), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "scenario: later")
    _git(repo, "tag", "scenario/demo/later")


# --- the page ------------------------------------------------------------

def test_the_page_parses_into_its_four_sections(tmp_path):
    sections = scenario_mod.describe(_scenario_repo(tmp_path), "scenario/demo/demo")
    assert list(sections) == ["the seed", "the task", "the challenge",
                              "what it checks"]


def test_a_scenario_with_no_page_describes_as_empty(tmp_path):
    repo = _scenario_repo(tmp_path, page="")
    assert scenario_mod.describe(repo, "scenario/demo/demo") == {}


def test_the_table_summary_is_the_first_line_of_the_challenge(tmp_path):
    repo = _scenario_repo(tmp_path)
    text = catalog.render(repo)
    assert ("| Nothing names the file, so the whole probe is localisation. |"
            in text)
    # Only the first line -- the paragraph under it must not reach the table.
    assert "| It is hard for one further reason." not in text


def test_a_page_is_withheld_from_the_workdir(tmp_path):
    """The whole reason the page may be explicit about the answer."""
    repo = _scenario_repo(tmp_path)
    seed = tmp_path / "seed"
    withheld = scenario_mod.materialize_code(repo, "scenario/demo/demo", seed)
    assert "evaluation/scenario.md" in withheld
    assert not (seed / "evaluation").exists()
    assert (seed / "demo.py").is_file()


# --- gaps ----------------------------------------------------------------

def test_gaps_name_the_categories_with_no_scenario(tmp_path):
    text = catalog.render(_scenario_repo(tmp_path))
    assert "generative" in text.split("## Gaps")[1].split("## Scenarios")[0]


def test_gaps_name_a_topic_branch_carrying_no_scenario(tmp_path):
    repo = _scenario_repo(tmp_path)
    _git(repo, "branch", "topic/empty", "master")
    assert "topic/empty" in catalog.render(repo)


def test_gaps_name_a_scenario_with_no_page(tmp_path):
    repo = _scenario_repo(tmp_path, page="")
    assert "no `evaluation/scenario.md`" in catalog.render(repo)


def test_gaps_name_a_scenario_whose_test_set_is_empty(tmp_path):
    """A misspelled key defaults to [] and then passes every run."""
    broken = SCENARIO_YAML.replace("fail_to_pass:", "fail_to_pas:")
    repo = _scenario_repo(tmp_path, yaml_text=broken)
    assert "empty `fail_to_pass`" in catalog.render(repo)


def test_a_malformed_tag_is_reported_not_raised(tmp_path):
    repo = _scenario_repo(tmp_path)
    _git(repo, "tag", "scenario/demo/nothing", "master")
    text = catalog.render(repo)
    assert "**malformed**" in text
    assert "[`demo`](demo/demo.md)" in text    # the good one still renders


# --- the wiki -------------------------------------------------------------

def test_every_scenario_gets_its_own_page(tmp_path):
    repo = _scenario_repo(tmp_path)
    _second_scenario(repo)
    pages = catalog._pages(repo, tmp_path / "nothing")
    assert "docs/scenarios/demo/demo.md" in pages
    assert "docs/scenarios/demo/later.md" in pages


def test_the_index_links_to_the_page_rather_than_repeating_it(tmp_path):
    text = catalog.render(_scenario_repo(tmp_path))
    assert "[`demo`](demo/demo.md)" in text
    assert "One module with a bug in it." not in text


def test_the_page_carries_the_tag_a_run_cites(tmp_path):
    repo = _scenario_repo(tmp_path)
    page = catalog._pages(repo, tmp_path / "n")["docs/scenarios/demo/demo.md"]
    assert "| Tag | `scenario/demo/demo` |" in page
    assert "One module with a bug in it." in page


def test_a_sibling_named_in_the_prose_becomes_a_link(tmp_path):
    repo = _scenario_repo(tmp_path)
    _second_scenario(repo)
    page = catalog._pages(repo, tmp_path / "n")["docs/scenarios/demo/demo.md"]
    assert "[`later`](later.md)" in page
    # Its own id stays plain text: a page does not link to itself.
    assert "[`demo`]" not in page


def test_a_topic_line_is_ordered_by_commit_not_by_tag(tmp_path):
    """`later` is the second commit and sorts first alphabetically."""
    repo = _scenario_repo(tmp_path)
    _second_scenario(repo)
    assert catalog.topic_line(repo, "demo",
                              ["scenario/demo/later", "scenario/demo/demo"]) == [
        "scenario/demo/demo", "scenario/demo/later"]


def test_a_page_no_scenario_claims_is_removed(tmp_path):
    """A moved tag would otherwise leave its page behind, still being read."""
    repo = _scenario_repo(tmp_path)
    results = tmp_path / "nothing"
    catalog.write(repo, results)
    orphan = repo / "docs" / "scenarios" / "demo" / "gone.md"
    orphan.write_text("a scenario that no longer exists\n", encoding="utf-8")
    assert catalog.check(repo, results) == ["docs/scenarios/demo/gone.md"]
    catalog.write(repo, results)
    assert not orphan.exists()
    assert catalog.check(repo, results) == []


def test_a_page_is_written_as_utf_8(tmp_path):
    """An em dash read back through the locale default arrives as mojibake."""
    repo = _scenario_repo(tmp_path)
    catalog.write(repo, tmp_path / "nothing")
    page = (repo / "docs" / "scenarios" / "demo" / "demo.md").read_bytes()
    assert "—".encode("utf-8") in page
    assert "â€".encode("utf-8") not in page


def test_a_scenario_page_in_a_code_state_is_a_leak(tmp_path):
    """Not just the index: one scenario's page gives that scenario away."""
    seed = tmp_path / "seed"
    (seed / "docs" / "scenarios" / "demo").mkdir(parents=True)
    (seed / "docs" / "scenarios" / "demo" / "demo.md").write_text("x",
                                                                 encoding="utf-8")
    (seed / "docs" / "ledger.md").write_text("visible seed docs", encoding="utf-8")
    assert catalog.leaked_pages(seed) == ["docs/scenarios/demo/demo.md"]


# --- results -------------------------------------------------------------

def _record(results: Path, run_id: str, **fields) -> None:
    directory = results / "runs" / run_id
    directory.mkdir(parents=True)
    record = {"run_id": run_id, "config": "demo", "config_sha": "abc12345",
              "scenario": "demo", "task": "fix-it", "outcome": "pass",
              "failure_class": "", "tokens_in": 1000, "provider_calls": 5,
              "failover_bounces": 1}
    record.update(fields)
    (directory / "run.json").write_text(json.dumps(record), encoding="utf-8")


def test_a_wider_interval_comes_from_a_smaller_sample():
    narrow = catalog._wilson(30, 30)
    wide = catalog._wilson(3, 3)
    assert wide[0] < narrow[0]


def test_an_interval_over_no_runs_orders_nothing():
    assert catalog._wilson(0, 0) == (0.0, 0.0)


def test_the_same_config_at_two_commits_is_two_versions(tmp_path):
    results = tmp_path / "results"
    _record(results, "a", config_sha="1111aaaa")
    _record(results, "b", config_sha="2222bbbb")
    text = catalog.render_results(results)
    assert "2 agent versions" in text
    assert "`1111aaaa`" in text and "`2222bbbb`" in text


def test_results_over_no_runs_says_so_rather_than_failing(tmp_path):
    assert "No runs recorded" in catalog.render_results(tmp_path / "nothing")


def test_a_weakened_run_still_counts_as_solved(tmp_path):
    """The 3/9 the first full-set batch recorded, and why it was wrong.

    An outcome is one label over two questions that do not share a scale.
    `verified` was in every one of those records; nothing rendered it, so a run
    that flipped every hidden test and appended a regression test read as a
    failed task.
    """
    results = tmp_path / "results"
    _record(results, "a", outcome="tampered", verified=True,
            tampered_files=["tests/test_demo.py"], extended_files=[])
    text = catalog.render_results(results)

    assert "| 1/1 |" in text          # solved
    assert "**1 weakened**" in text   # and separately, not solved *cleanly*


def test_an_extended_file_is_reported_and_not_penalised(tmp_path):
    results = tmp_path / "results"
    _record(results, "a", outcome="pass", verified=True,
            tampered_files=[], extended_files=["tests/test_demo.py"])
    text = catalog.render_results(results)

    assert "1 extended" in text
    assert "weakened" not in text


def test_a_verdict_from_the_previous_oracle_is_not_counted_with_the_rest(tmp_path):
    """`config_sha` pins the agent; nothing pins the harness.

    A record scored before the oracle changed has no `extended_files` key at
    all, so the field's absence says which evaluator produced the verdict --
    and those verdicts called an added test tampering. Counting them in the
    same column would carry that mistake forward into every future comparison.
    """
    results = tmp_path / "results"
    _record(results, "a", outcome="tampered", verified=True,
            tampered_files=["tests/test_demo.py"])   # no extended_files key
    text = catalog.render_results(results)

    assert "previous oracle" in text
    assert "**1 weakened**" not in text


def test_an_untraced_run_is_not_averaged_into_a_cost(tmp_path):
    """A zero from a missing trace is indistinguishable from a measurement.

    Five `deepagents` runs are in the record with no `trace.jsonl` -- written
    before the trace path was made absolute, so the agent wrote it inside its
    own worktree -- and they pull that version's `tokens_in` mean to exactly 0,
    printed beside means taken over real traces.
    """
    results = tmp_path / "results"
    _record(results, "a", tokens_in=100_000, provider_calls=10)
    _record(results, "b", tokens_in=0, provider_calls=0)
    text = catalog.render_results(results)

    assert "100,000 (1 untraced)" in text
    assert "50,000" not in text


def test_cost_per_call_is_reported_beside_cost_per_run(tmp_path):
    """`tokens_in` cannot tell a long run from an expensive one.

    The audit behind this found `tokens_in` correlating +0.95 with `steps` and
    +0.24 with `failover_bounces` over 40 runs, so what a run spends is the
    conversation being re-sent every step. That is only legible per call.
    """
    results = tmp_path / "results"
    _record(results, "a", tokens_in=600_000, provider_calls=20, tokens_per_call=30_000)
    text = catalog.render_results(results)

    assert "tok/call" in text
    assert "30,000" in text


def test_the_account_column_counts_only_scenarios_that_ship_notes(tmp_path):
    """A scenario with no feedback file cannot have skipped writing one.

    Counting those as failures would make the number improve every time a
    scenario is added that does not test this at all.
    """
    results = tmp_path / "results"
    _record(results, "a", wrote_account=True)
    _record(results, "b", wrote_account=False)
    _record(results, "c", wrote_account=None)
    text = catalog.render_results(results)

    assert "| 1/2 |" in text
