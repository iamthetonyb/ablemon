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
    XP_DURABLE_TASK_CHECKPOINT,
    XP_DURABLE_TASK_COMPLETE,
    XP_DURABLE_TASK_RESUME,
    XP_MANAGED_AGENT_SESSION,
    XP_RED_TEAM_SCAN,
    XP_OVERNIGHT_ITERATION,
    XP_MONITOR_RECOVERY,
    XP_BENCHMARK_PASS,
    load_buddy,
    record_collection_progress,
    save_buddy,
)

logger = logging.getLogger(__name__)

XP_SPECIALTY_BONUS = 4
XP_AETHER_ORCHESTRATION_BONUS = 12
XP_COMPRESSION_EFFICIENT = 8      # Good ratio + quality maintained
XP_COMPRESSION_SATURATED = 15     # 70%+ savings with quality preserved


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

    AUTO-CARE: The autonomous tick represents real system activity (cron jobs,
    research, monitoring). The buddy should reflect that the system is alive
    and working — not decay to "neglected" just because the user isn't chatting.

    - Always waters thirst (auto_tick +5) — the system IS active
    - Auto-feeds hunger when below 50 — autonomous work IS training
    - Always walks energy (self_explore +8) — exploring on its own

    Returns a status dict or None if no buddy exists.
    """
    buddy = load_buddy()
    if buddy is None:
        return None

    # Apply time-based needs decay
    mood_before = buddy.apply_needs_decay()

    # Passive XP drip — buddy is self-training / exploring on its own
    passive_xp = 5
    old_level = buddy.level
    buddy.award_xp(passive_xp)

    # ── Auto-care: keep buddy alive during autonomous operation ──

    # Always water — the system ticking IS activity (thirst +5)
    buddy.water("auto_tick")

    # Auto-feed when hungry — autonomous cron work IS self-training (hunger +12)
    needs = buddy.get_needs()
    auto_fed = False
    if needs.hunger < 50:
        buddy.feed("auto_care")
        auto_fed = True

    # Always walk — buddy explored on its own (energy +8)
    buddy.walk("self_explore")

    # Recompute mood after care
    mood = buddy.mood

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

    if auto_fed:
        logger.info(
            "Buddy %s auto-fed during tick (hunger was %.1f)",
            buddy.name, needs.hunger,
        )

    return {
        "name": buddy.name,
        "level": buddy.level,
        "xp": buddy.xp,
        "mood": mood,
        "mood_before": mood_before,
        "auto_fed": auto_fed,
        "leveled_up": leveled_up,
        "evolved": new_stage.value if new_stage else None,
        "legendary": legendary_title or None,
    }


def award_evolution_deploy_xp() -> Optional[int]:
    """Award XP when the evolution daemon deploys new weights.

    Also feeds the buddy — deploying improved weights IS the system
    eating well (self-improvement = nourishment).
    """
    buddy = load_buddy()
    if buddy is None:
        return None

    buddy.evolution_deploys += 1
    xp = 30  # Meaningful — the system improved itself
    buddy.award_xp(xp)

    # Evolution deploy = the system ate well (hunger +15)
    buddy.feed("evolution_deploy")
    # Evolution deploy = the system drank deeply (thirst +40)
    buddy.water("evolve")

    legendary_title = buddy.unlock_legendary()
    save_buddy(buddy)
    logger.info("Buddy %s gained %d XP from evolution deploy", buddy.name, xp)
    if legendary_title:
        logger.info("Buddy %s unlocked legendary form: %s", buddy.name, legendary_title)
    return xp


def award_distillation_xp(new_pairs: int = 0) -> Optional[int]:
    """Award XP when new distillation pairs are harvested.

    Also waters the buddy — harvesting training data IS the system
    absorbing knowledge (distillation = watering the roots).
    """
    buddy = load_buddy()
    if buddy is None:
        return None

    buddy.distillation_pairs += new_pairs
    xp = new_pairs * 3  # Each pair is valuable — it's training data
    buddy.award_xp(xp)

    # Distillation = the system absorbed knowledge (thirst +10)
    buddy.water("distillation")
    # Harvesting sessions = self-training snack (hunger +8)
    if new_pairs > 0:
        buddy.feed("session_harvest")

    legendary_title = buddy.unlock_legendary()
    save_buddy(buddy)
    logger.info("Buddy %s gained %d XP from %d new pairs", buddy.name, xp, new_pairs)
    if legendary_title:
        logger.info("Buddy %s unlocked legendary form: %s", buddy.name, legendary_title)
    return xp


def award_compression_xp(
    *,
    tokens_saved: int,
    compression_ratio: float,
    quality_maintained: bool = True,
) -> Optional[int]:
    """Award XP for efficient compression in a session.

    Called by compression-xp-award cron job based on aggregated metrics.
    tokens_saved: total tokens saved across compressed interactions.
    compression_ratio: avg ratio (lower = more savings).
    quality_maintained: True if audit scores stayed above threshold.
    """
    buddy = load_buddy()
    if buddy is None:
        return None

    if not quality_maintained or tokens_saved < 100:
        return 0

    # Scale XP by savings: base + bonus for saturation
    if compression_ratio < 0.3:
        xp = XP_COMPRESSION_SATURATED  # Excellent compression
    else:
        xp = XP_COMPRESSION_EFFICIENT  # Good compression

    # Bonus for large savings volumes
    xp += min(tokens_saved // 5000, 10)  # +1 XP per 5K tokens saved, max +10

    buddy.award_xp(xp)
    buddy.water("compression")  # Efficient token use = hydration

    save_buddy(buddy)
    logger.info(
        "Buddy %s gained %d XP from compression (ratio=%.2f, saved=%d tokens)",
        buddy.name, xp, compression_ratio, tokens_saved,
    )
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


# ── Phase 2 integration XP sources ───────────────────────────────────────────


def award_pentest_xp(findings_count: int = 0, critical_count: int = 0) -> Optional[int]:
    """Award XP for running a DeepTeam security scan.

    Red teaming = the system eating security training data.
    More findings = more learning material.
    """
    buddy = load_buddy()
    if buddy is None:
        return None

    xp = XP_RED_TEAM_SCAN + critical_count * 5
    buddy.award_xp(xp)
    buddy.feed("red_team_scan")
    buddy.walk("tool_use", domain="security")

    save_buddy(buddy)
    logger.info("Buddy %s gained %d XP from red team scan (%d findings)", buddy.name, xp, findings_count)
    return xp


def award_durable_task_xp(event_type: str = "checkpoint") -> Optional[int]:
    """Award XP for durable task lifecycle events.

    event_type: 'checkpoint' | 'complete' | 'resume'
    """
    buddy = load_buddy()
    if buddy is None:
        return None

    xp_map = {
        "checkpoint": XP_DURABLE_TASK_CHECKPOINT,
        "complete": XP_DURABLE_TASK_COMPLETE,
        "resume": XP_DURABLE_TASK_RESUME,
    }
    xp = xp_map.get(event_type, XP_DURABLE_TASK_CHECKPOINT)
    buddy.award_xp(xp)
    buddy.feed("durable_task")
    if event_type == "resume":
        buddy.walk("monitor_recovery")

    save_buddy(buddy)
    logger.info("Buddy %s gained %d XP from durable task (%s)", buddy.name, xp, event_type)
    return xp


def award_overnight_xp(iteration_count: int = 1, success: bool = True) -> Optional[int]:
    """Award XP for overnight orchestrator iterations.

    Overnight autonomous work = the buddy feeding and hydrating itself.
    """
    buddy = load_buddy()
    if buddy is None:
        return None

    xp = XP_OVERNIGHT_ITERATION * iteration_count
    if success and iteration_count >= 3:
        xp += 20  # Streak bonus for 3+ consecutive successes
    buddy.award_xp(xp)

    buddy.feed("overnight_iteration")
    buddy.water("overnight_cycle")
    buddy.walk("self_explore")

    save_buddy(buddy)
    logger.info("Buddy %s gained %d XP from overnight (%d iterations)", buddy.name, xp, iteration_count)
    return xp


def award_managed_agent_xp(session_duration_min: float = 0) -> Optional[int]:
    """Award XP for completing a managed agent session."""
    buddy = load_buddy()
    if buddy is None:
        return None

    xp = XP_MANAGED_AGENT_SESSION
    buddy.award_xp(xp)
    buddy.feed("auto_care")
    buddy.walk("self_explore")

    save_buddy(buddy)
    logger.info("Buddy %s gained %d XP from managed agent session (%.1f min)", buddy.name, xp, session_duration_min)
    return xp


def award_monitor_recovery_xp() -> Optional[int]:
    """Award XP when ExecutionMonitor intervention succeeds."""
    buddy = load_buddy()
    if buddy is None:
        return None

    xp = XP_MONITOR_RECOVERY
    buddy.award_xp(xp)
    buddy.walk("monitor_recovery")

    save_buddy(buddy)
    logger.info("Buddy %s gained %d XP from monitor recovery", buddy.name, xp)
    return xp


def award_benchmark_xp(model: str, domain: str, passed: bool = True) -> Optional[int]:
    """Award XP for behavioral benchmark results (pass/fail per model)."""
    buddy = load_buddy()
    if buddy is None:
        return None

    xp = XP_BENCHMARK_PASS if passed else 5
    buddy.award_xp(xp)
    buddy.feed("eval_pass")

    save_buddy(buddy)
    logger.info("Buddy %s gained %d XP from benchmark %s/%s (%s)", buddy.name, xp, model, domain, "pass" if passed else "fail")
    return xp


# ── First-install level seeding ───────────────────────────────────────────────

def seed_buddy_level_from_harvest(since_hours: int = 168) -> Optional[dict]:
    """Seed a new buddy's starting level from the user's existing interaction history.

    Called ONCE when the buddy is first created (total_interactions == 0 after init).
    Scans all harvesters to build a domain-confidence profile of the user's existing
    AI interactions — across ABLE CLI, Claude Code, Codex, ChatGPT, and any external
    tools — then awards starter XP so the buddy starts at a level reflecting the
    user's actual domain expertise.

    A security researcher gets a different starting experience than someone asking
    simple questions. The buddy level, species affinity, and domain badges all
    benefit from this one-time seeding.

    Returns a summary dict, or None if buddy doesn't exist / already seeded.
    """
    buddy = load_buddy()
    if buddy is None:
        return None

    # Only seed once — check the seeding flag in metadata
    if buddy.meta.get("level_seeded"):
        logger.debug("Buddy level already seeded — skipping")
        return None

    try:
        import sqlite3 as _sqlite3
        from pathlib import Path as _Path
        from able.core.routing.interaction_log import DEFAULT_DB_PATH
        from able.core.distillation.confidence_scorer import build_domain_confidence_profile
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz

        cutoff_iso = (_dt.now(_tz.utc) - _td(hours=since_hours)).isoformat()
        db_path = _Path(DEFAULT_DB_PATH)
        rows = []

        if db_path.exists():
            conn = _sqlite3.connect(str(db_path))
            conn.row_factory = _sqlite3.Row
            try:
                cursor = conn.execute(
                    """
                    SELECT domain, complexity_score, thinking_content, raw_input, raw_output,
                           guidance_needed, audit_score, actual_provider, selected_provider
                    FROM interaction_log
                    WHERE success = 1
                      AND raw_input IS NOT NULL
                      AND raw_output IS NOT NULL
                      AND timestamp >= ?
                    ORDER BY timestamp DESC
                    LIMIT 200
                    """,
                    (cutoff_iso,),
                )
                rows = [dict(r) for r in cursor.fetchall()]
            finally:
                conn.close()

        if not rows:
            logger.info("Buddy seeding: no interaction history found — starting fresh")
            buddy.meta["level_seeded"] = True
            save_buddy(buddy)
            return {"starter_xp": 0, "primary_domains": [], "rows_analyzed": 0}

        profile = build_domain_confidence_profile(rows)
        starter_xp = profile["starter_xp"]

        if starter_xp > 0:
            old_level = buddy.level
            buddy.award_xp(starter_xp)

            # Record primary domains as bonus_domains (species gets bonus XP in these)
            if profile["primary_domains"]:
                buddy.meta.setdefault("bonus_domains", [])
                for d in profile["primary_domains"]:
                    if d not in buddy.meta["bonus_domains"]:
                        buddy.meta["bonus_domains"].append(d)

            new_stage = buddy.check_evolution()
            legendary_title = buddy.unlock_legendary()

            if new_stage:
                buddy.evolve(new_stage)

            logger.info(
                "Buddy %s seeded: +%d XP from %d interactions → level %d (primary: %s)",
                buddy.name, starter_xp, len(rows), buddy.level,
                ", ".join(profile["primary_domains"]),
            )
            if buddy.level > old_level:
                logger.info("Buddy %s leveled up to %d from seeding!", buddy.name, buddy.level)
            if legendary_title:
                logger.info("Buddy %s unlocked legendary form from seeding: %s", buddy.name, legendary_title)

        buddy.meta["level_seeded"] = True
        buddy.meta["seed_profile"] = {
            "rows_analyzed": len(rows),
            "starter_xp": starter_xp,
            "primary_domains": profile["primary_domains"],
            "avg_confidence": profile["avg_confidence"],
        }
        save_buddy(buddy)

        return {
            "starter_xp": starter_xp,
            "primary_domains": profile["primary_domains"],
            "avg_confidence": profile["avg_confidence"],
            "rows_analyzed": len(rows),
            "final_level": buddy.level,
        }

    except Exception as exc:
        logger.warning("Buddy level seeding failed (non-fatal): %s", exc)
        return None
