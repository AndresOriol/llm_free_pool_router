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

from agent.code.prompt import (FS_TOOL_GUIDANCE, HEADLESS_AMBIGUITY,
                               HEADLESS_PREAMBLE, pool_identity_section,
                               workdir_section)

logger = logging.getLogger("harness.explore")

_TEMPLATE = Path(__file__).with_name("system_prompt.md")
DEEP_TEMPLATE = Path(__file__).with_name("deep_system_prompt.md")


def build(floor: int, members: int = 0,
          extra_sections: Optional[Sequence[str]] = None,
          template: Optional[Path] = None) -> str:
    """The full system prompt for one exploration.

    `template` selects the body. The default is this project's own
    ([system_prompt.md](system_prompt.md)), which describes the grounded-Gemini
    `web_search`/`read_url` pair and a method written here. The deep-research
    session passes [deep_system_prompt.md](deep_system_prompt.md) instead: a body
    carrying only the facts about *this* system -- which pool serves a call,
    where the jail's `/` is, that nobody is watching -- with the research method
    supplied by upstream's workflow sections as an `extra_section`
    ([session.orchestrator_prompt](session.py)).

    Two templates rather than one with a flag, because the difference is not a
    setting: the tools named in each body do not both exist in a given run, and
    a prompt describing a tool the agent does not have is a measured cause of
    failed calls ([6.5](../../docs/06-agent.md)).
    """
    result = (
        (template or _TEMPLATE).read_text(encoding="utf-8")
        .replace("{interactive_preamble}", HEADLESS_PREAMBLE)
        .replace("{ambiguity_guidance}", HEADLESS_AMBIGUITY)
        .replace("{filesystem_tool_guidance}", FS_TOOL_GUIDANCE)
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
