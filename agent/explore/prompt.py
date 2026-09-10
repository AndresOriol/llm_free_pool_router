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
from agent.explore import notes

logger = logging.getLogger("harness.explore")

_TEMPLATE = Path(__file__).with_name("system_prompt.md")

# What is *absent* -- and only that. Each tool explains itself in its own
# description, where the explanation is in front of the model at the moment it
# picks the tool rather than ten thousand characters earlier
# ([tools.py](tools.py)); a second copy here would be one more thing to keep
# true. What no description can carry is the shape of the gap, because the tools
# that would fill it are not there to describe it.
TOOL_SURFACE = """## What you cannot do

There is no shell, no `ls`, no `glob` and no `grep`, and the project tree is not
in front of you. You do not explore this repository: you are told which of its
files matter, and you find everything else out on the web. If an answer needs a
file nobody named, say so rather than guessing at a path.

`research_status` lists what your own research has written so far. It is the
only listing you get, and it is the one worth asking for."""


def build(floor: int, members: int = 0,
          research_dir: str = notes.DEFAULT_DIR,
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

    # After assembly, so a section built elsewhere is pointed at this run's
    # directory too ([notes.retarget](notes.py)).
    result = notes.retarget(result, research_dir)

    # A typo in the template would otherwise ship a literal `{placeholder}` to
    # the model, which reads as an instruction it cannot follow rather than as
    # a bug.
    unreplaced = re.findall(r"\{[a-z_]+\}", result)
    if unreplaced:
        logger.warning(f"System prompt has unreplaced placeholders: {unreplaced}")
    return result
