"""
XP engine — awards XP from real system activity.

Called by the interaction logger after each completion and by
the evolution daemon after each cycle.
"""

from __future__ import annotations

import logging
from typing import Optional

from .model import (
    BuddyState,
    Stage,
    XP_PER_INTERACTION,
    XP_COMPLEXITY_MULTIPLIER,
    XP_TOOL_EXECUTION,
    XP_APPROVAL_GRANTED,
    XP_DOMAIN_BONUS,
    load_buddy,
    record_collection_progress,
    save_buddy,
)

logger = logging.getLogger(__name__)


def award_interaction_xp(
    *,
    complexity_score: float = 0.0,
    used_tools: bool = False,
    approval_granted: bool = False,
    domain: str = "default",
) -> Optional[int]:
    """
    Award XP for a completed interaction.  Call after process_message.

    Returns the XP awarded, or None if no buddy exists.
    """
    buddy = load_buddy()
    if buddy is None:
        return None

    xp = XP_PER_INTERACTION
    xp += int(complexity_score * XP_COMPLEXITY_MULTIPLIER)

    if used_tools:
        xp += XP_TOOL_EXECUTION
    if approval_granted:
        xp += XP_APPROVAL_GRANTED

    # Species domain bonus
    bonus_domains = buddy.meta.get("bonus_domains", [])
    if domain in bonus_domains:
        xp += XP_DOMAIN_BONUS

    old_level = buddy.level
    buddy.award_xp(xp)
    buddy.total_interactions += 1

    # Update needs — each interaction is a sip of water
    buddy.water("interaction")
    if used_tools:
        buddy.walk("tool_use", domain=domain)
    elif domain and domain != "default":
        buddy.walk("new_domain", domain=domain)

    # Check evolution
    new_stage = buddy.check_evolution()
    leveled_up = buddy.level > old_level
    legendary_title = buddy.unlock_legendary()

    save_buddy(buddy)

    if leveled_up:
        logger.info(
            "Buddy %s leveled up to %d! (+%d XP)",
            buddy.name, buddy.level, xp,
        )

    if new_stage:
        buddy.evolve(new_stage)
        if not legendary_title:
            legendary_title = buddy.unlock_legendary()
        save_buddy(buddy)
        logger.info(
            "Buddy %s EVOLVED to stage %d (%s)!",
            buddy.name, new_stage.value, new_stage.name,
        )
    if legendary_title:
        logger.info(
            "Buddy %s unlocked legendary form: %s",
            buddy.name, legendary_title,
        )

    collection_update = record_collection_progress(
        domain,
        points=2 if used_tools else 1,
    )
    for unlocked in collection_update["new_buddies"]:
        logger.info("New buddy caught: %s (%s)", unlocked.name, unlocked.meta["label"])
    for badge in collection_update["new_badges"]:
        logger.info("Buddy badge unlocked: %s", badge["title"])
    if collection_update["easter_egg_unlocked"]:
        logger.info("Buddy collection milestone unlocked: full completion reached")

    return xp


def award_evolution_deploy_xp() -> Optional[int]:
    """Award XP when the evolution daemon deploys new weights."""
    buddy = load_buddy()
    if buddy is None:
        return None

    buddy.evolution_deploys += 1
    xp = 30  # Meaningful — the system improved itself
    buddy.award_xp(xp)
    legendary_title = buddy.unlock_legendary()
    save_buddy(buddy)
    logger.info("Buddy %s gained %d XP from evolution deploy", buddy.name, xp)
    if legendary_title:
        logger.info("Buddy %s unlocked legendary form: %s", buddy.name, legendary_title)
    return xp


def award_distillation_xp(new_pairs: int = 0) -> Optional[int]:
    """Award XP when new distillation pairs are harvested."""
    buddy = load_buddy()
    if buddy is None:
        return None

    buddy.distillation_pairs += new_pairs
    xp = new_pairs * 3  # Each pair is valuable — it's training data
    buddy.award_xp(xp)
    legendary_title = buddy.unlock_legendary()
    save_buddy(buddy)
    logger.info("Buddy %s gained %d XP from %d new pairs", buddy.name, xp, new_pairs)
    if legendary_title:
        logger.info("Buddy %s unlocked legendary form: %s", buddy.name, legendary_title)
    return xp
