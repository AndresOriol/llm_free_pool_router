"""Turning recorded Claude Code sessions into scenario raw material.

Every scenario in the set is meant to be drawn from a request someone actually
made, rather than from work invented to be testable
([design/generative-scenarios.md](../docs/design/generative-scenarios.md) 2 and
4). The transferable idea borrowed from CursorBench is that recovering *the
prompt that produced this commit* is a scenario factory, and it was filed as a
roadmap item because the raw material did not exist. It does: Claude Code keeps
an append-only JSONL per session under `~/.claude/projects/<slug>/<uuid>.jsonl`.

This module reads them and emits a corpus. It **interprets nothing** -- no
archetype labelling, no scenario generation, no judgement about what a session
was worth. It turns an append-only log into a turn list with the tool calls,
the files written and the shell commands run, so that reading 100+ sessions is
a grep rather than an afternoon. Everything downstream of that is authoring
work, and authoring is where the judgement belongs.

The five scenarios added for the generative batch came out of this, and the
archetypes that produced them are in [ARCHETYPES.md](ARCHETYPES.md) beside it.

    python -m evals mine --out <dir> [--sessions <dir>]

Nothing here writes to the sessions it reads.
"""

import collections
import json
from pathlib import Path

# Where Claude Code keeps them. One directory per project, slugged from its
# path; one JSONL per session, appended to as the session runs.
DEFAULT_SESSIONS = Path.home() / ".claude" / "projects"

EDIT_TOOLS = ("Edit", "Write", "NotebookEdit")
SHELL_TOOLS = ("Bash", "PowerShell")


def _blocks(message: dict) -> list:
    """A message's content blocks. Older records hold a bare string."""
    content = message.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return content if isinstance(content, list) else []


def read_jsonl(path: Path):
    """Records in file order, tolerating a truncated final line.

    The log is appended to while the session runs, so the last line of a
    session that is still open -- or that died -- is routinely half-written.
    """
    for line in path.open(encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def read_session(path: Path, project: str) -> dict:
    """One session as a turn list: what was asked, and what was done."""
    turns, branches, models, cwds = [], set(), set(), set()
    title = first = last = None

    for record in read_jsonl(path):
        kind = record.get("type")
        if record.get("gitBranch"):
            branches.add(record["gitBranch"])
        if record.get("cwd"):
            cwds.add(record["cwd"])
        stamp = record.get("timestamp")
        if stamp:
            first = first or stamp
            last = stamp
        if kind in ("custom-title", "ai-title"):
            title = record.get("customTitle") or record.get("aiTitle") or title

        message = record.get("message")
        if kind == "user" and isinstance(message, dict):
            for block in _blocks(message):
                if block.get("type") == "text" and block.get("text", "").strip():
                    turns.append({"role": "human", "ts": stamp,
                                  "branch": record.get("gitBranch"),
                                  "text": block["text"]})
        elif kind == "assistant" and isinstance(message, dict):
            models.add(message.get("model"))
            for block in _blocks(message):
                if block.get("type") == "text" and block.get("text", "").strip():
                    turns.append({"role": "assistant", "ts": stamp,
                                  "text": block["text"]})
                elif block.get("type") == "tool_use":
                    turns.append({"role": "tool", "ts": stamp,
                                  "name": block.get("name"),
                                  "input": block.get("input"),
                                  "id": block.get("id")})
        elif kind == "attachment":
            attachment = record.get("attachment") or {}
            if attachment.get("type") == "file":
                turns.append({"role": "attachment", "ts": stamp,
                              "path": attachment.get("filename")})

    return {"id": path.stem, "project": project, "file": str(path),
            "title": title, "start": first, "end": last,
            "branches": sorted(branches), "cwds": sorted(cwds),
            "models": sorted(m for m in models if m), "turns": turns}


def work_done(turns: list) -> tuple:
    """(files written, shell commands run).

    The two things that make a session recoverable as a scenario: which files
    the change landed in, and how it was checked.
    """
    files, commands = collections.Counter(), []
    for turn in turns:
        if turn["role"] != "tool":
            continue
        args = turn.get("input") or {}
        if turn["name"] in EDIT_TOOLS and args.get("file_path"):
            files[args["file_path"]] += 1
        elif turn["name"] in SHELL_TOOLS and args.get("command"):
            commands.append(args["command"])
    return files, commands


def mine(sessions_dir: Path, out_dir: Path) -> list:
    """Write one JSON per session plus an index, and return the index."""
    (out_dir / "sessions").mkdir(parents=True, exist_ok=True)
    index = []

    for project_dir in sorted(p for p in sessions_dir.iterdir() if p.is_dir()):
        for path in sorted(project_dir.glob("*.jsonl")):
            session = read_session(path, project_dir.name)
            files, commands = work_done(session["turns"])
            session["files_touched"] = files.most_common()
            session["commands"] = commands
            (out_dir / "sessions" / f"{session['id']}.json").write_text(
                json.dumps(session, ensure_ascii=False, indent=1),
                encoding="utf-8", newline="\n")

            humans = [t for t in session["turns"] if t["role"] == "human"]
            index.append({
                "id": session["id"], "project": session["project"],
                "title": session["title"], "start": session["start"],
                "end": session["end"], "branches": session["branches"],
                "cwds": session["cwds"], "human_turns": len(humans),
                "tool_calls": sum(1 for t in session["turns"]
                                  if t["role"] == "tool"),
                "files_touched": len(files),
                "top_files": [p for p, _ in files.most_common(8)],
                # The scenario factory in one field: what someone asked for,
                # beside the files it turned into.
                "first_prompt": humans[0]["text"] if humans else "",
            })

    index.sort(key=lambda row: row["start"] or "")
    (out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1),
        encoding="utf-8", newline="\n")
    return index
