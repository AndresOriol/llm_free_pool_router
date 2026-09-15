"""What every agent here is told about where it runs, and how any prompt is filled.

Each agent's job is its own Markdown (`agent/<name>/prompts/`). Where they run
is the same for all three, so it is written once, in `prompts/` beside this
file, and reaches their templates through `shared_values()`:

| placeholder | file | says |
| --- | --- | --- |
| `{interactive_preamble}` | `headless.md` | one task and nobody to ask: assume and proceed |
| `{ambiguity_guidance}` | `ambiguity.md` | pick a reading and note it; never run a command that waits on stdin |
| `{model_identity_section}` | `model_identity.md` | the model is a pool, and the floor is all the context it can count on |
| `{working_dir_section}` | `working_dir.md` | `/` is the project, and nothing is above it |
| `{filesystem_tool_guidance}` | `file_tools.md`, `running_commands*.md` | file tools over shell commands, and what `execute` will run |

Three of them are adapted from deepagents-code's `get_system_prompt` (MIT), and
each adaptation is a fact about this pool rather than a preference:

- **Headless always.** dcode defaults to an interactive TUI where the agent may
  ask and wait. Nobody is watching a run here, so the text takes the branch that
  says assume and proceed (docs/design/long-run-harness.md#3 R3).
- **Identity is a pool, not a model.** dcode names the model and its context
  window. Here the router picks per call and one run is routinely served by
  four or five models, so a name would be false by the second step. The floor
  every eligible member clears is the part that stays true
  ([4. Failover](../../docs/04-failover.md)).
- **Paths are rooted at `/`.** dcode tells the model to build absolute host
  paths. The backend here is a jail whose `/` *is* the workdir, so that
  instruction would fail every tool call.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional, Sequence

logger = logging.getLogger("harness.prompts")

HERE = Path(__file__).parent / "prompts"

_PYTHONS = ("python", "python3", "py")


def fill(path: Path, values: dict) -> str:
    """One Markdown file with its `{placeholders}` filled from `values`."""
    text = Path(path).read_text(encoding="utf-8").strip()
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    # A typo would ship a literal `{placeholder}`, which a model reads as an
    # instruction it cannot follow rather than as a bug.
    unfilled = re.findall(r"\{[a-z_]+\}", text)
    if unfilled:
        logger.warning(f"{Path(path).name} has unfilled placeholders: {unfilled}")
    return text


def _shared(name: str, values: Optional[dict] = None) -> str:
    return fill(HERE / name, values or {})


def running_commands(programs: Sequence[str]) -> str:
    """What `execute` will and will not do, or '' when nothing restricts it.

    Said before the first command, because a refusal is only read after a step
    has been spent on it -- and `&&` cost a step in every recorded run.

    The Python paragraph is there only when Python is on the list.
    `agent/improve` runs `git` alone, and when this told it to reach for
    `python -c`, 10 of its first 28 `execute` calls were refused
    ([19.4.1](../../docs/19-improvement-agent.md#1941-the-traps-that-are-already-known)).
    """
    if not programs:
        return ""
    text = _shared("running_commands.md", {"programs": ", ".join(programs)})
    if any(program in _PYTHONS for program in programs):
        text += "\n" + _shared("running_commands_python.md")
    return text


def shared_values(floor: int, members: int = 0,
                  programs: Sequence[str] = ()) -> dict:
    """`{placeholder: text}` for every row of the table above."""
    guidance = (_shared("file_tools.md"), running_commands(programs))
    return {
        "interactive_preamble": _shared("headless.md"),
        "ambiguity_guidance": _shared("ambiguity.md"),
        "model_identity_section": _shared(
            "model_identity.md", {"floor": f"{floor:,}", "members": members}),
        "working_dir_section": _shared("working_dir.md"),
        "filesystem_tool_guidance": "\n\n".join(g for g in guidance if g),
    }
