"""
Buddy nudge system — generates notifications when the buddy needs attention.

Used by the proactive engine and Telegram handler to send care reminders.
"""

from __future__ import annotations

import logging
from typing import Optional

from .model import BuddyState, BuddyNeeds, load_buddy, save_buddy

logger = logging.getLogger(__name__)


def check_nudge() -> Optional[str]:
    """
    Check if the buddy needs attention and return a nudge message.

    Returns None if the buddy is fine or doesn't exist.
    Call this periodically from the proactive engine or before sending
    Telegram responses.
    """
    buddy = load_buddy()
    if buddy is None:
        return None

    # Apply decay first
    buddy.apply_needs_decay()
    save_buddy(buddy)

    needs = buddy.get_needs()
    emoji = buddy.meta["emoji"]
    name = buddy.name
    nudges = []

    if needs.hunger < 20:
        nudges.append(f"starving! Run `/battle` to feed it")
    elif needs.hunger < 40:
        nudges.append(f"hungry — evals needed")

    if needs.thirst < 20:
        nudges.append(f"parched! Run `/evolve` to water it")
    elif needs.thirst < 40:
        nudges.append(f"thirsty — needs evolution cycle")

    if needs.energy < 20:
        nudges.append(f"exhausted! Try new domains")
    elif needs.energy < 40:
        nudges.append(f"low energy — explore more domains")

    if not nudges:
        return None

    return f"{emoji} {name}: {'; '.join(nudges)}"


def get_status_line(buddy: Optional[BuddyState] = None) -> str:
    """
    One-line status for embedding in Telegram responses.

    Returns empty string if buddy doesn't exist or is thriving.
    """
    if buddy is None:
        buddy = load_buddy()
    if buddy is None:
        return ""

    needs = buddy.get_needs()
    if needs.mood == "thriving":
        return ""

    emoji = buddy.meta["emoji"]
    mood_msg = needs.mood_message
    return f"\n{emoji} {buddy.name}: {mood_msg}"
