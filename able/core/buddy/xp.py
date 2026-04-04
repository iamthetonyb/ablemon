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
    Species,
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

XP_SPECIALTY_BONUS = 4
XP_AETHER_ORCHESTRATION_BONUS = 12


def _species_specialty_bonus(
    buddy: BuddyState,
    *,
    complexity_score: float,
    used_tools: bool,
    approval_granted: bool,
    domain: str,
    selected_tier: int | None,
) -> int:
    species = buddy.species_enum
    if species == Species.BLAZE and used_tools:
        return XP_SPECIALTY_BONUS
    if species == Species.WAVE and domain in {"research", "analysis", "data"} and complexity_score >= 0.55:
        return XP_SPECIALTY_BONUS
    if species == Species.ROOT and (domain in {"production", "infrastructure", "deploy"} or approval_granted):
        return XP_SPECIALTY_BONUS
    if species == Species.SPARK and domain in {"creative", "copywriting", "content"}:
        return XP_SPECIALTY_BONUS
    if species == Species.PHANTOM and domain in {"security", "audit", "threat"}:
        return XP_SPECIALTY_BONUS
    if species == Species.AETHER:
        bonus = 0
        if complexity_score >= 0.70:
            bonus += XP_SPECIALTY_BONUS
        if used_tools:
            bonus += XP_SPECIALTY_BONUS
        if selected_tier and selected_tier >= 4:
            bonus += XP_SPECIALTY_BONUS
        return min(bonus, XP_AETHER_ORCHESTRATION_BONUS)
    return 0


def award_interaction_xp(
    *,
    complexity_score: float = 0.0,
    used_tools: bool = False,
    approval_granted: bool = False,
    domain: str = "default",
    selected_tier: int | None = None,
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

    xp += _species_specialty_bonus(
        buddy,
        complexity_score=complexity_score,
        used_tools=used_tools,
        approval_granted=approval_granted,
        domain=domain,
        selected_tier=selected_tier,
    )

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


def buddy_autonomous_tick() -> Optional[dict]:
    """Periodic background tick — buddy 'takes a walk' while the user is away.

    Called every ~2h by the cron scheduler. Applies needs decay, awards a
    small passive XP drip, and checks for evolution / legendary transitions.
    Returns a status dict or None if no buddy exists.
    """
    buddy = load_buddy()
    if buddy is None:
        return None

    # Apply time-based needs decay
    mood = buddy.apply_needs_decay()

    # Passive XP drip — buddy is self-training / exploring on its own
    passive_xp = 5
    old_level = buddy.level
    buddy.award_xp(passive_xp)

    # Small energy boost — buddy walked around on its own
    buddy.walk("self_explore")

    # Check for stage evolution and legendary unlock
    new_stage = buddy.check_evolution()
    if new_stage:
        buddy.evolve(new_stage)
        logger.info(
            "Buddy %s EVOLVED to stage %d during autonomous walk!",
            buddy.name, new_stage.value,
        )
    legendary_title = buddy.unlock_legendary()
    if legendary_title:
        logger.info(
            "Buddy %s unlocked legendary form during autonomous walk: %s",
            buddy.name, legendary_title,
        )

    save_buddy(buddy)

    leveled_up = buddy.level > old_level
    if leveled_up:
        logger.info(
            "Buddy %s leveled up to %d during autonomous walk",
            buddy.name, buddy.level,
        )

    return {
        "name": buddy.name,
        "level": buddy.level,
        "xp": buddy.xp,
        "mood": mood,
        "leveled_up": leveled_up,
        "evolved": new_stage.value if new_stage else None,
        "legendary": legendary_title or None,
    }


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


# ── gstack sprint skill XP ────────────────────────────────────────

# XP awarded per gstack skill completion, mapped by engineering value
_GSTACK_SKILL_XP: dict[str, int] = {
    "review": 20,           # Code review — high value, catches bugs
    "qa": 20,               # Quality assurance — direct reliability
    "qa-only": 15,          # Report-only QA — still valuable
    "cso": 25,              # Security audit — Phantom specialty, critical
    "ship": 15,             # Ship workflow — deployment discipline
    "land-and-deploy": 15,  # Merge + deploy + verify
    "investigate": 20,      # Root-cause debugging — high skill
    "autoplan": 10,         # Auto-review pipeline
    "plan-design-review": 10,
    "design-review": 10,
    "plan-ceo-review": 10,
    "plan-eng-review": 10,
    "benchmark": 15,        # Performance regression detection
    "canary": 10,           # Post-deploy monitoring
    "retro": 10,            # Retrospective — learning from experience
    "office-hours": 10,     # Startup diagnostic
    "document-release": 5,  # Doc updates
    "setup-deploy": 5,      # One-time config
}

# Map gstack skills to buddy-relevant domains for species bonuses
_GSTACK_SKILL_DOMAIN: dict[str, str] = {
    "review": "coding",
    "qa": "coding",
    "qa-only": "coding",
    "cso": "security",
    "ship": "deploy",
    "land-and-deploy": "deploy",
    "investigate": "coding",
    "autoplan": "coding",
    "benchmark": "coding",
    "canary": "deploy",
    "retro": "research",
    "office-hours": "research",
    "document-release": "content",
}


def award_gstack_sprint_xp(
    skill: str,
    outcome: str = "success",
    learnings_count: int = 0,
) -> Optional[int]:
    """Award buddy XP for completing a gstack sprint skill.

    Args:
        skill: gstack skill name (e.g. "review", "qa", "cso")
        outcome: "success", "failure", or "partial"
        learnings_count: number of learnings captured during the session
    """
    buddy = load_buddy()
    if buddy is None:
        return None

    base_xp = _GSTACK_SKILL_XP.get(skill, 8)

    # Scale by outcome
    if outcome == "failure":
        base_xp = max(3, base_xp // 3)  # Still get some XP for trying
    elif outcome == "partial":
        base_xp = max(5, base_xp // 2)

    # Bonus XP for capturing learnings (knowledge compounds)
    learning_bonus = min(learnings_count * 3, 15)

    xp = base_xp + learning_bonus

    # Species domain bonus
    domain = _GSTACK_SKILL_DOMAIN.get(skill, "coding")
    xp += _species_specialty_bonus(
        buddy,
        complexity_score=0.6,  # Sprint skills are moderately complex
        used_tools=True,
        approval_granted=False,
        domain=domain,
        selected_tier=None,
    )

    old_level = buddy.level
    buddy.award_xp(xp)
    buddy.total_interactions += 1

    # Sprint work feeds the buddy (keeps it healthy)
    buddy.feed("gstack_sprint")
    buddy.walk("tool_use", domain=domain)

    new_stage = buddy.check_evolution()
    legendary_title = buddy.unlock_legendary()

    save_buddy(buddy)

    if buddy.level > old_level:
        logger.info(
            "Buddy %s leveled up to %d from gstack /%s! (+%d XP)",
            buddy.name, buddy.level, skill, xp,
        )
    if new_stage:
        buddy.evolve(new_stage)
        save_buddy(buddy)
        logger.info(
            "Buddy %s EVOLVED to stage %d from gstack sprint work!",
            buddy.name, new_stage.value,
        )
    if legendary_title:
        logger.info(
            "Buddy %s unlocked legendary form from gstack sprint: %s",
            buddy.name, legendary_title,
        )

    return xp
