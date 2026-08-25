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
import re
from pathlib import Path
from typing import Optional, Sequence

logger = logging.getLogger("harness.deep")

_TEMPLATE = Path(__file__).with_name("system_prompt.md")

# Kept from dcode: the two places a model reaches for a shell when a first-class
# tool exists. Both were worth saying out loud there and are worth more here,
# because a shell command that edits a file bypasses the backend's own checks.
_FS_TOOL_GUIDANCE = (
    "IMPORTANT: Use the specialized tools instead of shell commands:\n\n"
    "- `edit_file` over `sed`/`awk`\n"
    "- `write_file` over `echo`/heredoc"
)

_HEADLESS_PREAMBLE = (
    "You received a single task and must complete it fully and autonomously. "
    "There is no human available to answer follow-up questions, so do NOT ask "
    "for clarification — make reasonable assumptions and proceed."
)

_HEADLESS_AMBIGUITY = (
    "- Do NOT ask clarifying questions — there is no human to answer them. Make "
    "reasonable assumptions and proceed.\n"
    "- If you encounter ambiguity, choose the most reasonable interpretation and "
    "note your assumption briefly.\n"
    "- Always use non-interactive command variants — no human is available to "
    "respond to a prompt. Never run a command that blocks waiting on stdin."
)


def pool_identity_section(floor: int, members: int = 0) -> str:
    """The `### Model Identity` section, for a pool rather than a model.

    Says only what survives a reroute. The floor is what the strict context
    filter guarantees, so it is the one number the model can plan against
    (agent/deep/session.py).
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
          extra_sections: Optional[Sequence[str]] = None) -> str:
    """The full system prompt for one session."""
    template = _TEMPLATE.read_text(encoding="utf-8")

    result = (
        template
        .replace("{mode_description}",
                 "non-interactive (headless) mode — there is no human operator "
                 "monitoring your output in real time")
        .replace("{interactive_preamble}", _HEADLESS_PREAMBLE)
        .replace("{ambiguity_guidance}", _HEADLESS_AMBIGUITY)
        .replace("{filesystem_tool_guidance}", _FS_TOOL_GUIDANCE)
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
