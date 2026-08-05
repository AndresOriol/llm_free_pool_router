"""Role definitions: one narrow agent per job.

Each role is a separate LLM call with its own tool set and its own slice of the
blackboard. Nothing is shared between roles except the blackboard, so a role's
prompt size is set by its `sections` and `tools`, not by how long the run has
been going.

The split is deliberate rather than cosmetic. `route` holds no tools at all, so
the model deciding *what to do next* is never the same call as the one holding
`run_tests` or `replace_in_file` -- a model cannot wander into executing
something while it is supposed to be choosing. Keeping the two apart also keeps
each schema payload small enough for a 6,000-TPM pool member to serve.
"""

from __future__ import annotations

from dataclasses import dataclass

# Shared preamble. Every role pays for this, so it stays at three lines.
_COMMON = (
    "You work inside a Python project. The project root is `/`; all paths are "
    "relative to it and you cannot escape it. There is no shell.\n"
    "Answer with tool calls, not explanations. Be brief."
)


@dataclass(frozen=True)
class Role:
    name: str
    prompt: str
    tools: tuple           # tool names from tools.make_tools
    sections: tuple        # blackboard sections this role is allowed to see
    max_rounds: int = 2    # tool-call rounds before the role is cut off
    instruction: str = ""  # the ask, appended after the rendered blackboard


LOCATE = Role(
    name="locate",
    prompt=f"{_COMMON}\nYou find the files that a task concerns. You do not read "
           "or change them.",
    tools=("find_files", "search_code", "list_dir"),
    sections=("task",),
    max_rounds=3,
    instruction="Find the files this task concerns. Search for identifiers named "
                "in the task. Stop as soon as you have the likely files.",
)

INSPECT = Role(
    name="inspect",
    prompt=f"{_COMMON}\nYou read code and report what is wrong and what must "
           "change. You do not edit anything.",
    tools=("read_lines", "search_code"),
    sections=("task", "files", "test"),
    max_rounds=3,
    instruction="Read the relevant code. Then state, in under 100 words: the file, "
                "the exact current lines that are wrong, and what they should "
                "become. Quote the current code exactly.",
)

EDIT = Role(
    name="edit",
    prompt=f"{_COMMON}\nYou apply one code change, exactly as described. You do "
           "not run tests and you do not explore.",
    tools=("replace_in_file", "create_file"),
    sections=("task", "notes", "edits", "test"),
    max_rounds=2,
    instruction="Apply the change described above using replace_in_file. "
                "`old_text` must match the file exactly. Make the smallest change "
                "that works.",
)

# Holds no tools on purpose: the call that decides what happens next must not be
# able to make anything happen.
ROUTE = Role(
    name="route",
    prompt="You direct a coding workflow. You have no tools. You reply with "
           "exactly one word and nothing else.",
    tools=(),
    sections=("task", "notes", "edits", "test", "log"),
    max_rounds=1,
    instruction="The tests still fail. Reply with ONE word:\n"
                "EDIT - the fix is understood but was applied wrong or incompletely\n"
                "INSPECT - we need to look at the code again\n"
                "LOCATE - we are working on the wrong file\n"
                "GIVEUP - the task cannot be completed\n"
                "One word only.",
)

ROLES = {r.name: r for r in (LOCATE, INSPECT, EDIT, ROUTE)}

# What `route` may answer, mapped to the next state. Anything else is treated as
# unparseable and the caller falls back to a deterministic default -- small
# models do not reliably honour "one word only".
ROUTE_CHOICES = {
    "EDIT": "edit",
    "INSPECT": "inspect",
    "LOCATE": "locate",
    "GIVEUP": "giveup",
}
