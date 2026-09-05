"""Local JSON storage for UI sessions and conversation history.

Saves sessions, messages, and statuses in `.ui_data/sessions.json`.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(".ui_data")
SESSIONS_FILE = DATA_DIR / "sessions.json"


def _ensure_storage() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not SESSIONS_FILE.exists():
        with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)


def _load_all() -> dict:
    _ensure_storage()
    try:
        with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_all(data: dict) -> None:
    _ensure_storage()
    with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_all_sessions() -> list[dict]:
    data = _load_all()
    sessions = list(data.values())
    # Sort by updated_at or created_at descending
    sessions.sort(key=lambda s: s.get("updated_at", s.get("created_at", "")), reverse=True)
    return sessions


def get_session(session_id: str) -> dict | None:
    data = _load_all()
    return data.get(session_id)


def create_session(title: str = "New Task") -> str:
    data = _load_all()
    session_id = str(uuid.uuid4())[:8]
    now = datetime.utcnow().isoformat()
    data[session_id] = {
        "id": session_id,
        "title": title,
        "created_at": now,
        "updated_at": now,
        "messages": [], # List of {"role": "user"|"assistant"|"system", "content": "...", "status": "completed"|"working"|"error"}
        "status": "idle" # idle, working, completed, error
    }
    _save_all(data)
    return session_id


def update_session(session_id: str, messages: list[dict], status: str = "idle", title: str | None = None) -> None:
    data = _load_all()
    if session_id in data:
        data[session_id]["messages"] = messages
        data[session_id]["status"] = status
        data[session_id]["updated_at"] = datetime.utcnow().isoformat()
        if title:
            data[session_id]["title"] = title
        _save_all(data)


def delete_session(session_id: str) -> None:
    data = _load_all()
    if session_id in data:
        del data[session_id]
        _save_all(data)
