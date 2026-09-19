"""What every agent here is told about where it runs, and how any prompt is filled.

Each agent's job is its own Markdown (`agent/<name>/prompts/`). Where they run
is the same for all three, so it is written once, in `prompts/` beside this
file, and reaches their templates through `shared_values()`:

| placeholder | file | says |
| --- | --- | --- |
| `{interactive_preamble}` | `headless.md` | one task and nobody to ask: assume and proceed |
| `{ambiguity_guidance}` | `ambiguity.md` | pick a reading and note it; never run a command that waits on stdin |
| `{model_identity_section}` | `model_identity.md` | the model is a pool, and the floor is all the context it can count on |
| `{working_dir_section}` | `working_dir.md` | `/` is the project, nothing is above it, and which shell `execute` runs |
| `{filesystem_tool_guidance}` | `file_tools.md` | prefer the file tools over shell commands for reading and editing |

Three of them are adapted from deepagents-code's `get_system_prompt` (MIT), and
each adaptation is a fact about this pool rather than a preference:

- **Headless always.** dcode defaults to an interactive TUI where the agent may
  ask and wait. Nobody is watching a run here, so the text takes the branch that
  says assume and proceed (docs/design/long-run-harness.md#3-what-helpful-requires-draft--v3, R3).
- **Identity is a pool, not a model.** dcode names the model and its context
  window. Here the router picks per call and one run is routinely served by
  four or five models, so a name would be false by the second step. The floor
  every eligible member clears is the part that stays true
  ([Failover](../../docs/pool/failover.md)).
- **Paths are rooted at `/`.** dcode tells the model to build absolute host
  paths. The backend here runs in `virtual_mode`, where `/` *is* the workdir, so
  that instruction would fail every file tool call.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger("harness.prompts")

HERE = Path(__file__).parent / "prompts"


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


def shell() -> str:
    """What `subprocess.run(shell=True)` runs a command with on this host.

    deepagents' `LocalShellBackend` runs `execute` that way and never names the
    shell, so a model left to guess assumes POSIX. On Windows it is `cmd.exe`,
    and the recorded eval runs spent 30% of their `execute` calls on `python -c`
    subprocess wrappers working around the guess.
    """
    if os.name == "nt":
        return "`" + Path(os.environ.get("COMSPEC", "cmd.exe")).name + "` (Windows)"
    return "`/bin/sh`"


def shared_values(floor: int, members: int = 0) -> dict:
    """`{placeholder: text}` for every row of the table above."""
    return {
        "interactive_preamble": _shared("headless.md"),
        "ambiguity_guidance": _shared("ambiguity.md"),
        "model_identity_section": _shared(
            "model_identity.md", {"floor": f"{floor:,}", "members": members}),
        "working_dir_section": _shared("working_dir.md", {"shell": shell()}),
        "filesystem_tool_guidance": _shared("file_tools.md"),
    }
