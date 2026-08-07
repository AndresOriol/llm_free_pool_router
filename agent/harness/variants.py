"""Pipeline variants: different agent architectures over the same roles.

The harness exists to trade turns for context size, but *how many* turns and
*which* decisions are worth a model call are open questions. Each variant here
answers them differently, and each is selectable by name so it can be measured
as its own configuration rather than argued about.

Select with `HARNESS_VARIANT=<name>`; default is `v1-pipeline`.

Findings for each are recorded in docs/06-agent.md#613-architecture-variants.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.harness.roles import EDIT, INSPECT, LOCATE, ROUTE, Role, _COMMON

# A merged locate+inspect for v3: one role that both finds and reads. Fewer
# handoffs, but it carries four tool schemas instead of two or three, and has to
# hold "find" and "understand" in one prompt.
INVESTIGATE = Role(
    name="investigate",
    prompt=f"{_COMMON}\nYou find the code a task concerns and report what must "
           "change. You do not edit anything.",
    tools=("find_files", "search_code", "read_lines"),
    sections=("task", "files", "exec"),
    max_rounds=4,
    instruction="Find and read the code this task concerns. Then state, in under "
                "100 words: the file, the exact current lines that are wrong, and "
                "what they should become. Quote the current code exactly.",
)

# v5 trims locate's tool set. `list_dir` is redundant with find_files('**/*'),
# and every extra tool is both schema tokens and one more thing a small model
# can pick wrongly.
LOCATE_LEAN = Role(
    name="locate", prompt=LOCATE.prompt, tools=("find_files", "search_code"),
    sections=LOCATE.sections, max_rounds=2, instruction=LOCATE.instruction,
)


@dataclass(frozen=True)
class Variant:
    """One architecture. Everything the state machine varies lives here."""

    name: str
    note: str
    # "pipeline": fixed locate -> inspect -> edit -> [test] -> route.
    # "orchestrated": hub and spoke, an orchestrator chooses every step.
    topology: str = "pipeline"
    locate: Role = LOCATE
    inspect: Role = INSPECT
    edit: Role = EDIT
    route: Role = ROUTE
    merged: bool = False
    # Skip the `locate` role entirely when the project has at most this many
    # source files: in a small tree, listing files is a `glob` call, not a
    # reasoning problem. 0 disables the shortcut.
    seed_threshold: int = 0
    # Deterministic edit retries after a failing test before `route` is asked.
    # The common case after a near-miss edit is another edit, and paying a model
    # call to be told so is waste.
    fast_retries: int = 0
    # Spend the role's last round with no tools bound, so a role that used every
    # round on tool calls is still forced to say what it found. Without this a
    # role can do all its work and report nothing: the loop ends holding a
    # tool-calling response, whose text content is empty.
    force_summary: bool = False
    # Refuse to enter `edit` with an empty `notes` section. An edit role with
    # write tools and nothing to act on improvises -- in one observed run it
    # created a junk `read_files.py` to explore with, because exploring was the
    # only thing left to do with the tools it had.
    require_note: bool = False
    # Orchestrated only: refuse a `DONE` that no successful run backs up. An
    # unverified `done` is the `stopping` failure class wearing a confident
    # face, and one deterministic push-back is cheaper than a lost run.
    verify_before_done: bool = True
    # Orchestrated runs spend a cycle per decision, not per edit-test round, so
    # they need a larger budget to reach the same amount of work.
    max_cycles_hint: int = 0
    # Orchestrated only: go straight from a successful edit to execution instead
    # of asking. Observed: orchestrators keep picking EDIT after an edit already
    # applied, never verify, and burn the budget. "Check what you just changed"
    # is the one transition that is always right, so it is not worth a call.
    auto_execute_after_edit: bool = True


V1 = Variant(
    name="v1-pipeline",
    note="locate -> inspect -> edit -> test -> route. The original.",
)

V2 = Variant(
    name="v2-seeded",
    note="Seeds the file list by glob on small trees, skipping `locate`.",
    seed_threshold=25,
)

V3 = Variant(
    name="v3-merged",
    note="One `investigate` role instead of locate + inspect.",
    merged=True,
    inspect=INVESTIGATE,
)

V4 = Variant(
    name="v4-fastretry",
    note="One deterministic edit retry after a failed test before consulting route.",
    fast_retries=1,
)

V5 = Variant(
    name="v5-lean",
    note="v2 + v4 + a two-tool locate. The combination worth shipping if each part holds.",
    locate=LOCATE_LEAN,
    seed_threshold=25,
    fast_retries=1,
)

V6 = Variant(
    name="v6-guarded",
    note="v5 plus a forced summary round and a no-note guard on edit. Targets the "
         "observed 'inspect reports nothing, edit then improvises' cycle.",
    locate=LOCATE_LEAN,
    seed_threshold=25,
    fast_retries=1,
    force_summary=True,
    require_note=True,
)

V7 = Variant(
    name="v7-orchestrated",
    note="Hub and spoke: an orchestrator with the widest view routes to explore, "
         "plan, edit or execute after every step. Execution becomes an agent "
         "rather than a fixed step, so the run can check things other than the "
         "test suite and can replan when a result surprises it.",
    topology="orchestrated",
    force_summary=True,
    max_cycles_hint=12,
)

V8 = Variant(
    name="v8-session",
    note="A session rather than a task: an orchestrator briefs explore/write/"
         "execute/document/review, the run commits incrementally on its own "
         "branch, journals every step so a crash resumes, and ends by updating "
         "the docs and writing a rationale into the project's notes.",
    topology="session",
    max_cycles_hint=24,
)

VARIANTS = {v.name: v for v in (V1, V2, V3, V4, V5, V6, V7, V8)}
DEFAULT = V1.name


def get(name: str | None) -> Variant:
    if not name:
        return VARIANTS[DEFAULT]
    if name not in VARIANTS:
        raise SystemExit(f"Unknown HARNESS_VARIANT {name!r}. "
                         f"Choose from: {', '.join(sorted(VARIANTS))}")
    return VARIANTS[name]
