"""The rationale: what the human actually reads.

Built from the journal, so every claim in it is a step that was recorded or a
command that ran. The session does not get to narrate: if it is not in the
journal, it does not appear here.
"""

from __future__ import annotations

import re
from pathlib import Path


def _changed_files(diff: str) -> set:
    """Paths a unified diff touches.

    Kept out of the f-string it used to live in: the doubled escaping there
    (`\\\\+` inside a raw string) made the pattern match a literal backslash, so
    every rationale ever written reported "Files changed: 0" while the diff
    plainly listed files. A number nobody can check is worse than no number.
    """
    return set(re.findall(r"^\+\+\+ b/(.+)$", diff or "", re.MULTILINE))


def write_rationale(path: Path, session_id: str, task: str, steps: list,
                    outcome: str, diff: str, branch: str) -> str:
    """Build the session's account of itself, from the journal."""
    evidence = [(cmd, code) for step in steps for cmd, code in (step.get("evidence") or [])]
    open_questions = [s for s in steps
                      if s.get("status") in {"BLOCKED", "INSUFFICIENT_CONTEXT"}]

    lines = [
        f"# Session {session_id}",
        "",
        f"- **Outcome:** {outcome}",
        f"- **Branch:** {branch or '(not a git repo)'}",
        f"- **Steps:** {len(steps)}",
        f"- **Files changed:** {len(_changed_files(diff))}",
        "",
        "## What was asked",
        "",
        task.strip()[:1_500],
        "",
        "## What was done",
        "",
    ]
    for step in steps:
        lines.append(f"{step['n']}. **{step['action']}** ({step['status']}) — "
                     f"{(step.get('finding') or '').strip()[:300]}")
    lines += ["", "## Evidence", ""]
    if evidence:
        lines += [f"- `{cmd}` → exit {code}" for cmd, code in evidence]
    else:
        lines.append("- Nothing was executed. No claim here is backed by a run.")

    lines += ["", "## Open questions", ""]
    if open_questions:
        # R3: a session that got stuck says so in writing rather than ending
        # quietly, because nobody is watching until morning.
        lines += [f"- ({s['action']}) {(s.get('finding') or '').strip()[:300]}"
                  for s in open_questions]
    else:
        lines.append("- None recorded.")

    text = "\n".join(lines) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def summarize(steps: list, outcome: str) -> str:
    """The two-line version, for the notes file."""
    did = [s for s in steps if s.get("action") in {"write", "document"}
           and s.get("status") == "DONE"]
    ran = [c for s in steps for c in (s.get("evidence") or [])]
    return (f"{len(steps)} step(s), {len(did)} change(s) applied, "
            f"{len(ran)} command(s) run. Outcome: {outcome}.")
