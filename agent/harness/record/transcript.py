"""The transcript: what each model turn was handed, and what it said back.

The journal records what a step *concluded*. This records what it was given and
what it actually replied -- and only the second pair separates a node that
reasoned badly from a node that was briefed badly. A review that cannot tell
those apart can name the failure but not its cause, which is the difference
between a report and a recommendation.

Deliberately not folded into the journal: a rendered prompt runs to a few
kilobytes, and the journal is the crash-resume substrate, re-read line by line
on every resume. Keeping the bulk out of it leaves that path cheap.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

TRANSCRIPT_DIR = "steps"


class Transcript:
    """Every model turn written out verbatim, one file each."""

    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        # A resumed session continues the numbering instead of overwriting the
        # turns that came before the crash.
        self.turn = len(list(self.dir.glob("*.md")))

    def write(self, role: str, result, brief=None) -> Path:
        self.turn += 1
        lines = [f"# Turn {self.turn:02d} - {role}", "",
                 f"_{datetime.now(timezone.utc).isoformat()}_"]
        if brief is not None:
            lines += ["", "## Brief it was given", "",
                      f"- **ACTION:** {brief.action}",
                      f"- **GOAL:** {brief.goal or '(none)'}",
                      f"- **CONTEXT:** {brief.context or '(none)'}",
                      f"- **DONE_WHEN:** {brief.done_when or '(none)'}"]
        lines += ["", "## Prompt", "", "```",
                  result.prompt or "(not captured)", "```",
                  "", "## Raw reply", "", "```", result.text or "(empty)", "```"]
        if result.calls:
            lines += ["", "## Tool calls", ""]
            for index, call in enumerate(result.calls, 1):
                args = json.dumps(call.get("args") or {}, default=str)
                lines += [f"{index}. `{call.get('name')}` {args[:500]}",
                          "",
                          "   ```",
                          _indent(str(call.get("output") or "")[:800]),
                          "   ```",
                          ""]
        path = self.dir / f"{self.turn:02d}-{role}.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


def _indent(text: str) -> str:
    return "\n".join("   " + line for line in text.splitlines())
