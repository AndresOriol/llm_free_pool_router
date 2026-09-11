"""The improvement agent's system prompt.

Filled the way the other agents fill theirs, and sharing what every agent
here is told about where it runs (`agent/runtime/prompts/`) -- the pool
identity, the jail's `/`, the headless preamble, and what `execute` will run.
Those are facts about *this project* rather than about any one agent's job, so
a second copy of them would be a second thing to keep true.

What is not shared is the body. The coding prompt is about editing a repository
and the explorer's is about citing the web; this one is about *reading what a
run left behind* and its failure mode is different again. It fails by writing a
confident diagnosis with no lever, or by closing an issue nobody re-measured --
so most of the template's length goes on what counts as evidence and on what
may be concluded from it ([system_prompt.md](system_prompt.md)).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

from agent.runtime.prompts import fill, shared_values

_TEMPLATE = Path(__file__).with_name("system_prompt.md")


def build(floor: int, members: int = 0,
          extra_sections: Optional[Sequence[str]] = None,
          programs: Sequence[str] = ()) -> str:
    """The full system prompt for one improvement pass."""
    result = fill(_TEMPLATE, shared_values(floor, members, programs))
    if extra_sections:
        result = result.rstrip() + "\n\n" + "\n\n".join(extra_sections) + "\n"
    return result
