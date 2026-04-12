"""
Buddy nudge system — generates notifications when the buddy needs attention.

Used by the proactive engine and Telegram handler to send care reminders.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

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
    emoji = buddy.display_emoji
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

    emoji = buddy.display_emoji
    mood_msg = needs.mood_message
    return f"\n{emoji} {buddy.name}: {mood_msg}"


def format_buddy_footer(update: Dict[str, Any]) -> str:
    """Format a one-line buddy footer for Telegram messages.

    Shows XP gain, level-ups, evolutions, and mood on every message.
    Returns empty string if update is None or empty.

    Examples:
        🌿 Atlas Lv5 +14 XP · thriving
        🌿 Atlas leveled up! Lv4 → Lv5 +14 XP 🎉
        🌿 Atlas EVOLVED to Stage 2! +14 XP 🔥
        🌿 Atlas Lv5 +14 XP · hungry — evals needed
    """
    if not update:
        return ""

    emoji = update.get("buddy_emoji", "🥚")
    name = update.get("buddy_name", "Buddy")
    xp = update.get("xp", 0)
    level = update.get("level", 1)
    mood = update.get("mood", "")
    leveled_up = update.get("leveled_up", False)
    old_level = update.get("old_level", level)
    evolved = update.get("evolved")
    legendary = update.get("legendary")

    if legendary:
        return f"\n{emoji} {name} unlocked legendary form: **{legendary}** +{xp} XP ✨"

    if evolved:
        return f"\n{emoji} {name} EVOLVED to Stage {evolved}! +{xp} XP 🔥"

    if leveled_up:
        return f"\n{emoji} {name} leveled up! Lv{old_level} → Lv{level} +{xp} XP 🎉"

    # Normal: show XP gain + mood
    mood_suffix = f" · {mood}" if mood else ""
    return f"\n{emoji} {name} Lv{level} +{xp} XP{mood_suffix}"
