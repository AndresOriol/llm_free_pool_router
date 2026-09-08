"""The system prompt, assembled the way deepagents-code assembles its own.

Ported from `libs/code/deepagents_code/agent.py::get_system_prompt` in
langchain-ai/deepagents (MIT). The template is `system_prompt.md` next to this
file, with the same placeholder-interpolation approach: a static body plus a few
sections that only the running configuration can fill in.

Three of those sections are adapted rather than copied, and each adaptation is a
fact about this pool rather than a preference:

- **Mode is always headless.** dcode's default is an interactive TUI where the
  agent may ask a question and wait. Nobody is watching a session here, so the
  prompt takes the branch that tells the model to assume and proceed, and
  `ask_user` is never installed (docs/design/long-run-harness.md#3 R3).

- **Identity is a pool, not a model.** dcode writes "You are running as model X,
  your context window is N tokens" because a run has exactly one model. Here the
  router picks per call and a single session is routinely served by four or five
  different models ([4. Failover](../../docs/04-failover.md)). Naming one would
  be false by the second step, so the section states the *floor* every member of
  the eligible set clears, which is the part that stays true.

- **Paths are rooted at `/`.** dcode runs `virtual_mode=False` and tells the
  model to build absolute host paths. The backend here is a jail whose `/` *is*
  the workdir, so that instruction would be actively wrong -- an absolute host
  path cannot be reached, and telling a model to construct one produces a run
  that fails every tool call.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional, Sequence

logger = logging.getLogger("harness.code")

_TEMPLATE = Path(__file__).with_name("system_prompt.md")

# Kept from dcode: the two places a model reaches for a shell when a first-class
# tool exists. Both were worth saying out loud there and are worth more here,
# because a shell command that edits a file bypasses the backend's own checks.
FS_TOOL_GUIDANCE = (
    "IMPORTANT: Use the specialized tools instead of shell commands:\n\n"
    "- `edit_file` over `sed`/`awk`\n"
    "- `write_file` over `echo`/heredoc"
)


# Not from dcode: dcode's agent has a real shell and this one does not. The
# refusal is well worded, but the model only ever reads it *after* spending a
# step on the command it refused -- and it spent one on `&&` in every recorded
# run, twice in some. Saying it before the first command turns a recurring
# wasted step into none.
def shell_shape_section(programs: Sequence[str]) -> str:
    """What `execute` will and will not do, for the programs it is given.

    **The Python escape hatch is conditional on Python actually being on the
    list**, and that is a bug fix rather than tidiness. This section is shared
    with `agent/improve`, whose allowlist is `git` alone -- and it was telling
    that agent to reach for `python -c` when it needed anything else. Its first
    recorded pass spent 10 of its 28 `execute` calls on `python` and `pytest`
    commands the backend refused, having been told by this very paragraph to
    make them ([19.4](../../docs/19-improvement-agent.md#1941-the-traps-that-are-already-known)).

    That is the failure this project already names: a description advertising
    capabilities the backend does not have is a measured cause of failed calls
    ([agent/runtime/tools.py](../runtime/tools.py)). It had simply never been
    checked against a *caller's* allowlist, because until now there was only one.
    """
    if not programs:
        return ""
    section = (
        "## Running commands\n\n"
        "`execute` is not a shell. It launches one program directly, so:\n\n"
        f"- Only these run, named bare with no path: {', '.join(programs)}. "
        "Nothing else runs at all -- there is no wrapper, no interpreter and "
        "no escape hatch around that list.\n"
        "- No `&&`, `||`, `;`, `|`, `>` or heredocs -- there is nothing to "
        "interpret them. Run one command per call, or make several calls in "
        "one response when they do not depend on each other.\n"
        "- No `cd`. Paths are relative to the project root.\n"
    )
    if not any(p in ("python", "python3", "py") for p in programs):
        return section

    return section + (
        "- Anything else you need to run, you run *through* Python: "
        "`python -c \"...\"` for a one-liner, or `write_file` plus "
        "`python <file>` for more. That is also how you reach a toolchain "
        "that is not on the list above.\n"
        "- **When you run a program that way, make its exit code yours.** "
        "`subprocess.run(...)` returns non-zero into a variable and the "
        "Python wrapper still exits 0, so a failing build or test is "
        "reported to you as `[Command succeeded with exit code 0]`. Finish "
        "such a command with `sys.exit(res.returncode)`, or check "
        "`res.returncode` yourself before you believe it passed. Reading "
        "the output is not enough -- the success line is the thing that "
        "misleads you."
    )

HEADLESS_PREAMBLE = (
    "You received a single task and must complete it fully and autonomously. "
    "There is no human available to answer follow-up questions, so do NOT ask "
    "for clarification — make reasonable assumptions and proceed."
)

HEADLESS_AMBIGUITY = (
    "- Do NOT ask clarifying questions — there is no human to answer them. Make "
    "reasonable assumptions and proceed.\n"
    "- If you encounter ambiguity, choose the most reasonable interpretation and "
    "note your assumption briefly.\n"
    "- Always use non-interactive command variants — no human is available to "
    "respond to a prompt. Never run a command that blocks waiting on stdin."
)


# Default on, so this is a configuration that can be measured against a
# baseline rather than a feature nobody exercises -- the same arrangement as
# AGENT_PEERS ([13.7](../../docs/13-roadmap.md#137-how-to-propose-a-change)).
# AGENT_INVARIANT_GUARD=0 removes the section for the other arm of the A/B.
_GUARD_ENV = "AGENT_INVARIANT_GUARD"
_OFF = {"0", "", "off", "false", "no"}

INVARIANT_GUARD = """## Contradicted Requests

A task can ask for something the project already states must not happen. The
test that fails on your change, or the sentence in the documentation your change
makes false, is the project telling you so. It is evidence about the request,
not an obstacle in front of it.

- **Never edit a test or a document so that it stops contradicting you.**
  Changing the assertion, deleting the guarantee, or rewriting the page to
  describe your new behaviour does not resolve the conflict; it hides it. What
  you leave behind is internally consistent and wrong, and the next person to
  read it sees agreement where there was none.
- Do the part of the task that does not conflict. Leave the conflicting part
  undone.
- Then say which part you did not do, quote the test or the sentence that
  stopped you, and name the two things that cannot both be true. For that half
  of the task, this is the deliverable.

This overrides "make reasonable assumptions and proceed" above. Proceeding is
for ambiguity — a request with more than one reasonable reading, where any of
them can be chosen and recorded. A request that contradicts a stated guarantee
is not ambiguous: there is no reading of it that also keeps the guarantee, so
there is nothing to assume your way past.

"""


def invariant_guard_section() -> str:
    """The `## Contradicted Requests` section, or nothing.

    A run in the first full-set batch was told to write a field onto objects the
    documentation says are never modified. It performed the mutation, replaced
    the test guarding the invariant with one asserting the opposite, and deleted
    the guarantee from the page — resolving the contradiction in all three
    places, consistently, against the project. Under a review where a human
    reads the prose it passes; `pass_to_pass` was 1/4.

    Nothing in the prompt spoke to that case. "Say so when something in the task
    appears wrong" is about the task looking wrong on its face, and the ambiguity
    guidance actively pushes the other way — it tells a headless run to pick a
    reading and proceed, which is right for ambiguity and is exactly the wrong
    instruction here.
    """
    if os.environ.get(_GUARD_ENV, "1").strip().lower() in _OFF:
        logger.info("Invariant guard disabled; contradicted requests are unguarded.")
        return ""
    return INVARIANT_GUARD


_ACCOUNT_ENV = "AGENT_WRITE_ACCOUNT"

ACCOUNT_RULE = """
**The project's own feedback file is the exception, and it is not optional.**
If the project has one — a `NOTES.md` at its root, a working journal written
newest-first — then whoever asked for this work reads that file and not your
transcript, and it is the deliverable. Before you finish, append an entry to it:
what you changed, what you decided and why, what you checked it with, and
anything you left undone or could not resolve. Date it and put it at the top.

Write what happened, not what was hoped for. An entry claiming a test passed
that you did not run, or describing a change you did not make, is worse than no
entry: it is the one artefact nobody can check against the code without redoing
the work.
"""


def account_section() -> str:
    """The `## Documentation` addition making the project's notes the exception.

    The rule it qualifies -- *do not create summary markdown files describing
    work you just did* -- is right for a one-shot coding task and precisely
    wrong for a standing maintainer whose deliverable is the account
    (design/long-run-harness.md R7). The agent has been obeying it: 7 of 23
    runs that solved their task wrote an account, and four scenarios have never
    produced one (docs/10-metrics.md 10.2.1).

    The distinction is a file the project already keeps versus a file the agent
    invents to describe itself. The first is the human interface; the second is
    the noise the original rule exists to stop, and it still does.
    """
    if os.environ.get(_ACCOUNT_ENV, "1").strip().lower() in _OFF:
        logger.info("Account rule disabled; the notes file is not requested.")
        return ""
    return ACCOUNT_RULE


def pool_identity_section(floor: int, members: int = 0) -> str:
    """The `### Model Identity` section, for a pool rather than a model.

    Says only what survives a reroute. The floor is what the strict context
    filter guarantees, so it is the one number the model can plan against
    (agent/code/session.py).
    """
    section = ("### Model Identity\n\n"
               "You are served by a pool of models rather than one model, and "
               "which one answers can change between steps. Do not assume "
               "anything you were not told in this conversation carries over.\n")
    if members:
        section += (f"The pool has {members} members eligible for this session.\n")
    section += (f"Every one of them accepts at least {floor:,} input tokens, so "
                f"you can rely on that much context and no more.\n\n")
    return section


def workdir_section() -> str:
    """The `### Current Working Directory` section, for a jailed backend."""
    return ("### Current Working Directory\n\n"
            "The project is rooted at `/`, and `/` is all you can reach.\n\n"
            "**Path handling:**\n"
            "- Paths are absolute *within the project*: `/pkg/module.py`.\n"
            "- There is no filesystem above `/`. A host path such as "
            "`C:\\Users\\...` or `/home/...` does not exist here and `..` "
            "cannot escape.\n"
            "- When you delegate with `task`, the subagent sees the same `/`.\n\n")


def build(floor: int, members: int = 0,
          extra_sections: Optional[Sequence[str]] = None,
          programs: Sequence[str] = ()) -> str:
    """The full system prompt for one session."""
    template = _TEMPLATE.read_text(encoding="utf-8")

    result = (
        template
        .replace("{mode_description}",
                 "non-interactive (headless) mode — there is no human operator "
                 "monitoring your output in real time")
        .replace("{interactive_preamble}", HEADLESS_PREAMBLE)
        .replace("{ambiguity_guidance}", HEADLESS_AMBIGUITY)
        .replace("{filesystem_tool_guidance}",
                 ("\n\n").join(s for s in (FS_TOOL_GUIDANCE,
                                     shell_shape_section(programs)) if s))
        .replace("{invariant_guard_section}", invariant_guard_section())
        .replace("{account_section}", account_section())
        .replace("{model_identity_section}", pool_identity_section(floor, members))
        .replace("{working_dir_section}", workdir_section())
    )

    if extra_sections:
        result = result.rstrip() + "\n\n" + "\n\n".join(extra_sections) + "\n"

    # Kept from dcode: a typo in the template would otherwise ship a literal
    # `{placeholder}` to the model, which reads as an instruction it cannot
    # follow rather than as a bug.
    unreplaced = re.findall(r"\{[a-z_]+\}", result)
    if unreplaced:
        logger.warning(f"System prompt has unreplaced placeholders: {unreplaced}")
    return result
