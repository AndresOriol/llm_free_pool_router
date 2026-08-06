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
    sections=("task", "files", "test"),
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

VARIANTS = {v.name: v for v in (V1, V2, V3, V4, V5)}
DEFAULT = V1.name


def get(name: str | None) -> Variant:
    if not name:
        return VARIANTS[DEFAULT]
    if name not in VARIANTS:
        raise SystemExit(f"Unknown HARNESS_VARIANT {name!r}. "
                         f"Choose from: {', '.join(sorted(VARIANTS))}")
    return VARIANTS[name]
