"""The explorer's system prompt.

Assembled the same way `agent/code/prompt.py` assembles the coding agent's, and
sharing three of its sections outright -- the pool identity, the jail's `/`, and
the headless preamble are facts about *this project*, not about coding, so a
second copy of them would be a second thing to keep true.

What is not shared is the body. The coding prompt is ported from
`deepagents-code` and is about editing a repository; this one is about the web
and about leaving a written record, which is a different job with a different
failure mode. The coding agent fails by breaking the build. This one fails by
writing something confident and unsourced, so the template spends most of its
length on citation and on saying what could not be found.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional, Sequence

from agent.code.prompt import (HEADLESS_AMBIGUITY, HEADLESS_PREAMBLE,
                               pool_identity_section, workdir_section)

logger = logging.getLogger("harness.explore")

_TEMPLATE = Path(__file__).with_name("system_prompt.md")

# The coding prompt's `FS_TOOL_GUIDANCE` used to sit here. It is three lines
# telling an agent to prefer `edit_file` over `sed` -- true, and addressed to an
# agent that has a shell to be tempted by. This one has none, and what it needs
# said instead is what it *does* have, because the surface it is offered is
# smaller than the one the framework's own tool descriptions assume
# ([tools.py](tools.py)).
TOOL_SURFACE = """## What you have

Six capabilities, and nothing else. Read this as the shape of the job:

- **`tavily_search`** — ask the web a question and get the pages themselves
  back, converted to text. There is no separate "open the page" step and no
  summary standing in for one: what you read is the source.
- **`think_tool`** — say what the last search established, what is still
  missing, and whether to search again. Use it after every search. It is the
  only step in this loop that is purely about deciding, and skipping it is how a
  run makes thirteen searches without noticing the twelfth added nothing.
- **`write_todos`** — the plan, and the state of it. It survives the
  summarization that eventually eats the conversation, so a long run remembers
  what it set out to answer.
- **`task`** — hand one topic to a research sub-agent with a fresh context. This
  is how a broad question gets divided, and it is the only way searching
  happens: the pages land in the sub-agent's context, not yours.
- **`read_file`** — one file, by exact path. Your own notes (listed below on
  every turn) and any project file the request named.
- **`write_file`, `edit_file`** — the deliverable. Write early, extend as you
  go, and never overwrite a note about another question.

There is no shell, no `ls`, no `glob` and no `grep`. You do not explore this
project; you are told which of its files matter, and you find things out on the
web."""


def build(floor: int, members: int = 0,
          extra_sections: Optional[Sequence[str]] = None) -> str:
    """The full system prompt for one exploration.

    The body ([system_prompt.md](system_prompt.md)) carries only the facts about
    *this* system -- which pool serves a call, where the jail's `/` is, that
    nobody is watching. The research *method* is not here: it arrives as an
    `extra_section` from [session.orchestrator_prompt](session.py), ported from
    upstream ([15.8](../../docs/15-explorer.md#158-the-deep-research-port)).

    That split is the point. Our own method was measured against upstream's and
    lost, so the half we keep writing is the half upstream cannot know.
    """
    result = (
        _TEMPLATE.read_text(encoding="utf-8")
        .replace("{interactive_preamble}", HEADLESS_PREAMBLE)
        .replace("{ambiguity_guidance}", HEADLESS_AMBIGUITY)
        .replace("{tool_surface_section}", TOOL_SURFACE)
        .replace("{model_identity_section}", pool_identity_section(floor, members))
        .replace("{working_dir_section}", workdir_section())
    )

    if extra_sections:
        result = result.rstrip() + "\n\n" + "\n\n".join(extra_sections) + "\n"

    # A typo in the template would otherwise ship a literal `{placeholder}` to
    # the model, which reads as an instruction it cannot follow rather than as
    # a bug.
    unreplaced = re.findall(r"\{[a-z_]+\}", result)
    if unreplaced:
        logger.warning(f"System prompt has unreplaced placeholders: {unreplaced}")
    return result
