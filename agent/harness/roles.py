"""Role definitions: one narrow agent per job.

A role is a separate LLM call with its own tool set and its own slice of the
blackboard. Nothing is shared between roles except that blackboard, so a role's
prompt size is set by its `sections` and `tools`, not by how long the run has
been going -- which is what lets step 40 cost what step 1 cost, and is the whole
reason this exists instead of one growing conversation.

Two fields carry most of the design:

- **`sections`** is the context budget. A role sees exactly what it declares and
  nothing else; the orchestrator makes up the difference by writing facts into
  the brief (agent/harness/envelope.py). Context is pushed down, never pulled up.
- **`min_context`** is a claim about the *job*, not the request. A role whose
  work is judgement over a wide view declares a floor and the router honours it
  (llm_router/router.py). Splitting work to fit the narrowest pool member is what
  makes this cheap; doing it to a role that needs breadth is what makes it stupid.

The orchestrator holds no tools, on purpose: the call that decides what happens
next must not be able to make anything happen.
"""

from __future__ import annotations

from dataclasses import dataclass

# Shared preamble. Every role pays for this, so it stays at three lines.
_COMMON = (
    "You work inside a Python project. The project root is `/`; all paths are "
    "relative to it and you cannot escape it. There is no shell.\n"
    "Answer with tool calls, not explanations. Be brief."
)

# Only the wide-context members of the pool clear this. Groq tops out at 12,000.
WIDE = 50_000


@dataclass(frozen=True)
class Role:
    name: str
    prompt: str
    tools: tuple           # tool names from tools.make_tools
    sections: tuple        # blackboard sections this role is allowed to see
    max_rounds: int = 2    # tool-call rounds before the role is cut off
    instruction: str = ""  # the ask, when no brief is written for this step
    # Is this role's value its *text* (a report) or its *tool effects* (an
    # action)? Only a reporting role benefits from a forced no-tools final
    # round. Forcing one on an acting role steals the round it needed to act:
    # an edit role given two rounds, one of them tool-less, spent the first
    # thinking and then could only describe the fix it never applied.
    reports: bool = True
    # See the module docstring. 0 means "anything in the pool will do".
    min_context: int = 0


# ---------------------------------------------------------------------------
# The hub.
# ---------------------------------------------------------------------------

ORCHESTRATE = Role(
    name="orchestrate",
    prompt="You direct a team of coding agents working on one project. You have "
           "no tools; the others do the work. Your only job is to choose who "
           "acts next and to tell them what they need to know.",
    tools=(),
    sections=("task", "plan", "files", "notes", "edits", "diff", "exec", "log"),
    max_rounds=1,
    min_context=WIDE,
    instruction="Choose the next action and write the brief for it. Reply in "
                "exactly this form:\n\n"
                "ACTION: EXPLORE | WRITE | EXECUTE | DOCUMENT | REVIEW | DONE | GIVEUP\n"
                "GOAL: <one sentence: what this step must achieve>\n"
                "CONTEXT: <the facts that step needs, copied out in full -- it "
                "cannot see anything you do not write here>\n"
                "DONE_WHEN: <how that step knows it has finished>\n\n"
                "EXPLORE finds and reads code. WRITE changes it. EXECUTE runs "
                "things to check whether it works. DOCUMENT updates the "
                "documentation to match the change. REVIEW checks the work is "
                "right before finishing. DONE only after a successful run and a "
                "review.",
)


# ---------------------------------------------------------------------------
# The workers. Each owns one capability and sees almost nothing.
# ---------------------------------------------------------------------------

EXPLORE = Role(
    name="explore",
    prompt=f"{_COMMON}\nYou explore a codebase and report what you found. You "
           "cannot change anything and you never claim that you did -- observed: "
           "an explore role reporting it had implemented a fix and run the "
           "tests, holding neither an edit tool nor a shell.",
    tools=("find_files", "search_code", "read_lines", "list_dir"),
    sections=("task",),
    max_rounds=3,
)

WRITE = Role(
    name="write",
    prompt=f"{_COMMON}\nYou apply code changes, exactly as briefed. You do not "
           "run tests and you do not explore.",
    # read_lines earns its schema here: `replace_in_file` needs `old_text` to
    # match the file exactly, and a writer that cannot look reports BLOCKED --
    # observed, verbatim: "no tool for reading files is provided".
    tools=("read_lines", "replace_in_file", "create_file"),
    sections=("task", "files"),
    max_rounds=4,
    reports=False,
)

EXECUTE = Role(
    name="execute",
    prompt=f"{_COMMON}\nYou decide what to run to find out whether the code "
           "works, and you run it. Prefer the project's own tests. When they "
           "do not cover the question, write a small throwaway script and run "
           "that. You never edit the project's own files.",
    tools=("run_command", "create_file"),
    sections=("task", "edits"),
    max_rounds=3,
    reports=False,
)

DOCUMENT = Role(
    name="document",
    prompt=f"{_COMMON}\nYou keep documentation true to the code. You read the "
           "change that was made and update the docs that the change makes "
           "wrong. You never change code.",
    tools=("read_lines", "find_files", "replace_in_file", "create_file"),
    sections=("task", "diff", "edits"),
    max_rounds=4,
    min_context=WIDE,
    reports=False,
)

REVIEW = Role(
    name="review",
    prompt=f"{_COMMON}\nYou review a change before it is called done. You judge "
           "whether it does what the task asked, not whether it is elegant. You "
           "never change anything.",
    tools=("read_lines", "search_code"),
    sections=("task", "diff", "notes", "exec"),
    max_rounds=3,
    min_context=WIDE,
)

# The orchestrator is deliberately absent: it is the hub, dispatched by the
# graph rather than chosen as a destination.
ROLES = {r.name: r for r in (EXPLORE, WRITE, EXECUTE, DOCUMENT, REVIEW)}

# Roles whose value is what they *say*; the rest are judged by what they did.
REPORTING = frozenset(r.name for r in ROLES.values() if r.reports)
