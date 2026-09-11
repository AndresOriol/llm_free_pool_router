"""The improvement agent's system prompt.

Assembled the way `agent/explore/agent.py` assembles the explorer's, and
sharing the same three sections from `agent/code/prompt.py` -- the pool
identity, the jail's `/`, and the headless preamble. Those are facts about
*this project* rather than about any one agent's job, so a second copy of them
would be a second thing to keep true.

What is not shared is the body. The coding prompt is about editing a repository
and the explorer's is about citing the web; this one is about *reading what a
run left behind* and its failure mode is different again. It fails by writing a
confident diagnosis with no lever, or by closing an issue nobody re-measured --
so most of the template's length goes on what counts as evidence and on what
may be concluded from it ([system_prompt.md](system_prompt.md)).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional, Sequence

from agent.code.prompt import (FS_TOOL_GUIDANCE, HEADLESS_AMBIGUITY,
                               HEADLESS_PREAMBLE, pool_identity_section,
                               shell_shape_section, workdir_section)

logger = logging.getLogger("harness.improve")

_TEMPLATE = Path(__file__).with_name("system_prompt.md")


def build(floor: int, members: int = 0,
          extra_sections: Optional[Sequence[str]] = None,
          programs: Sequence[str] = ()) -> str:
    """The full system prompt for one improvement pass."""
    result = (
        _TEMPLATE.read_text(encoding="utf-8")
        .replace("{interactive_preamble}", HEADLESS_PREAMBLE)
        .replace("{ambiguity_guidance}", HEADLESS_AMBIGUITY)
        .replace("{filesystem_tool_guidance}",
                 "\n\n".join(s for s in (FS_TOOL_GUIDANCE,
                                         shell_shape_section(programs)) if s))
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
