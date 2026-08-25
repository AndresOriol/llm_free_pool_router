"""Number and time formatting shared by the terminal table and the HTML page."""

from datetime import datetime, timezone
from typing import Optional


def compact(value: float) -> str:
    """512400 -> "512.4K". Token budgets are quoted in thousands everywhere else."""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(round(value))


def scaled(name: str, value: float) -> str:
    """Tokens read better rounded; requests are small integers and don't."""
    return compact(value) if name.startswith("t") or name == "tokens" else str(round(value))


def percent(ratio: Optional[float]) -> str:
    if ratio is None:
        return "-"
    if 0 < ratio < 0.01:
        return "<1%"
    return f"{round(ratio * 100)}%"


def iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def ago(seconds: float, now: float) -> str:
    delta = max(0.0, now - seconds)
    if delta < 90:
        return f"{round(delta)}s ago"
    if delta < 5400:
        return f"{round(delta / 60)}m ago"
    if delta < 172_800:
        return f"{round(delta / 3600)}h ago"
    return f"{round(delta / 86_400)}d ago"


def duration(seconds: Optional[float]) -> str:
    """Seconds until a budget refills, in the units a person would say."""
    if seconds is None:
        return ""
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"
