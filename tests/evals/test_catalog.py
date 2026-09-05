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
That the bug is gone and nothing else broke.
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
    assert "scenario/demo/demo" in text        # the good one still renders


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
