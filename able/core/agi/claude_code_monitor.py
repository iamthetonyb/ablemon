"""
Claude Code Monitor — bridges Claude Code statusline data into ABLE.

Reads state from ~/.able/claude_code_state.json (written by the statusline hook)
and provides:
- Rate limit awareness for routing decisions
- Incremental session harvest triggers
- Active session tracking for the proactive engine
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_STATE_PATH = Path.home() / ".able" / "claude_code_state.json"
_HARVEST_MARKER = Path.home() / ".able" / "last_harvested_session.txt"


def read_state() -> dict:
    """Read the current Claude Code state from the statusline bridge file."""
    try:
        if _STATE_PATH.exists():
            return json.loads(_STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.debug("Failed to read Claude Code state: %s", e)
    return {}


def get_active_session_path() -> Optional[str]:
    """Return the transcript_path of the currently active Claude Code session."""
    state = read_state()
    path = state.get("transcript_path")
    if path and Path(path).exists():
        return path
    return None


def get_session_cost() -> float:
    """Return the total cost USD of the current Claude Code session."""
    state = read_state()
    cost = state.get("cost", {})
    return cost.get("total_cost_usd", 0.0)


def get_rate_limits() -> dict:
    """Return rate limit info from the current Claude Code session."""
    state = read_state()
    return state.get("rate_limits", {})


def should_avoid_claude_api() -> bool:
    """
    Return True if Claude API usage should be avoided based on rate limits.

    Checks the 5-hour rolling window — if usage is above 80%, ABLE should
    prefer non-Anthropic providers for T4 requests to avoid competing
    with the user's active Claude Code session.
    """
    limits = get_rate_limits()
    five_hour = limits.get("five_hour", {})
    used_pct = five_hour.get("used_percentage", 0)
    return used_pct >= 80


def get_context_window_usage() -> float:
    """Return context window usage percentage (0-100) of active session."""
    state = read_state()
    ctx = state.get("context_window", {})
    return ctx.get("used_percentage", 0.0)


def get_model_info() -> dict:
    """Return model info from the active Claude Code session."""
    state = read_state()
    return state.get("model", {})


def get_new_session_to_harvest() -> Optional[str]:
    """
    Return the transcript_path if it hasn't been harvested yet, else None.

    Used by the proactive engine to trigger incremental harvest on session end
    or session change.
    """
    path = get_active_session_path()
    if not path:
        return None
    try:
        last = _HARVEST_MARKER.read_text().strip() if _HARVEST_MARKER.exists() else ""
        if last == path:
            return None  # already harvested
        return path
    except OSError:
        return None


def mark_session_harvested(path: str) -> None:
    """Mark a session transcript as harvested so we don't re-process it."""
    try:
        _HARVEST_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _HARVEST_MARKER.write_text(path)
    except OSError as e:
        logger.warning("Failed to write harvest marker: %s", e)
