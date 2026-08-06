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
    # Is this role's value its *text* (a report) or its *tool effects* (an
    # action)? Only a reporting role benefits from a forced no-tools final
    # round. Forcing one on an acting role steals the round it needed to act:
    # an edit role given two rounds, one of them tool-less, spent the first
    # thinking and then could only describe the fix it never applied.
    reports: bool = True


# ---------------------------------------------------------------------------
# Hub-and-spoke roles. The orchestrator is the hub: it is consulted after every
# worker, holds the widest view of the run, and holds no tools. The workers are
# narrow and each owns one capability.
# ---------------------------------------------------------------------------

EXPLORE = Role(
    name="explore",
    prompt=f"{_COMMON}\nYou explore a codebase and report what you found. You "
           "never change anything.",
    tools=("find_files", "search_code", "read_lines", "list_dir"),
    sections=("task", "plan", "notes"),
    max_rounds=3,
    instruction="Gather what the plan asks for. Then state what you found in "
                "under 100 words, quoting the exact lines that matter.",
)

EXECUTE = Role(
    name="execute",
    prompt=f"{_COMMON}\nYou run commands to check whether code works. You do not "
           "edit anything. Only `python` and `pytest` exist; there is no shell.",
    tools=("run_tests",),
    sections=("task", "plan", "edits"),
    max_rounds=2,
    reports=False,
    instruction="Run the command that checks this task. Usually "
                '`python -m pytest`. Then say in one line whether it passed and, '
                "if not, what failed.",
)

PLAN = Role(
    name="plan",
    prompt="You plan coding work. You have no tools. You write a short numbered "
           "plan and nothing else.",
    tools=(),
    sections=("task", "files", "notes", "exec"),
    max_rounds=1,
    instruction="Write a numbered plan of at most 4 steps for finishing this "
                "task. Be concrete about which file each step touches.",
)

# The hub. No tools, on purpose: the call that decides what happens next must
# not be able to make anything happen.
ORCHESTRATE = Role(
    name="orchestrate",
    prompt="You direct a coding agent. You have no tools; other agents do the "
           "work. You reply with exactly one word and nothing else.",
    sections=("task", "plan", "files", "notes", "edits", "exec", "log"),
    tools=(),
    max_rounds=1,
    instruction="Choose the next action. Reply with ONE word:\n"
                "EXPLORE - we need to find or read code\n"
                "PLAN - the work needs breaking down before acting\n"
                "EDIT - we know what to change; change it\n"
                "EXECUTE - code changed, or we need to see whether it works\n"
                "DONE - the task is complete and verified by a successful run\n"
                "GIVEUP - the task cannot be completed\n"
                "One word only.",
)

ACTIONS = {
    "EXPLORE": "explore",
    "PLAN": "plan",
    "EDIT": "edit",
    "EXECUTE": "execute",
    "DONE": "done",
    "GIVEUP": "giveup",
}

# ---------------------------------------------------------------------------
# Fixed-pipeline roles.
# ---------------------------------------------------------------------------

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
    sections=("task", "files", "exec"),
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
    sections=("task", "plan", "notes", "edits", "exec"),
    max_rounds=3,
    reports=False,
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
    sections=("task", "notes", "edits", "exec", "log"),
    max_rounds=1,
    instruction="The tests still fail. Reply with ONE word:\n"
                "EDIT - the fix is understood but was applied wrong or incompletely\n"
                "INSPECT - we need to look at the code again\n"
                "LOCATE - we are working on the wrong file\n"
                "GIVEUP - the task cannot be completed\n"
                "One word only.",
)

ROLES = {r.name: r for r in (LOCATE, INSPECT, EDIT, ROUTE,
                             EXPLORE, EXECUTE, PLAN, ORCHESTRATE)}

# What `route` may answer, mapped to the next state. Anything else is treated as
# unparseable and the caller falls back to a deterministic default -- small
# models do not reliably honour "one word only".
ROUTE_CHOICES = {
    "EDIT": "edit",
    "INSPECT": "inspect",
    "LOCATE": "locate",
    "GIVEUP": "giveup",
}
