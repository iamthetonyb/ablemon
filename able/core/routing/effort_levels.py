"""
User effort levels for routing control (Claurst pattern).

Lets users override ABLE's automatic complexity scoring with explicit
effort preferences. Maps to ABLE's 5-tier routing system.

Levels:
- low:    Force Tier 1 (cheapest/fastest)
- medium: Auto routing (default — ABLE decides)
- high:   Bias toward Tier 2+ (prefer quality)
- max:    Force Tier 4 (Opus, session-scoped for cost protection)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class EffortLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MAX = "max"


@dataclass
class EffortOverride:
    """Result of applying effort level to a complexity score."""
    original_score: float
    adjusted_score: float
    forced_tier: Optional[int]  # None = use adjusted score, int = force this tier
    level: EffortLevel


# Tier mapping for forced levels
_FORCED_TIERS = {
    EffortLevel.LOW: 1,
    EffortLevel.MAX: 4,
}

# Score adjustments for non-forced levels
_SCORE_ADJUSTMENTS = {
    EffortLevel.MEDIUM: 0.0,   # No change, auto routing
    EffortLevel.HIGH: 0.15,    # Bias toward higher tiers
}


def get_effort_level() -> EffortLevel:
    """Get current effort level from environment or config.

    Checks:
    1. ABLE_EFFORT_LEVEL env var
    2. Default: medium (auto)
    """
    env_val = os.environ.get("ABLE_EFFORT_LEVEL", "").lower().strip()
    try:
        return EffortLevel(env_val)
    except ValueError:
        return EffortLevel.MEDIUM


def apply_effort(score: float, level: Optional[EffortLevel] = None) -> EffortOverride:
    """Apply effort level to a complexity score.

    Args:
        score: Original complexity score (0.0-1.0)
        level: Explicit level, or None to read from env/config

    Returns:
        EffortOverride with adjusted score and optional forced tier.
    """
    if level is None:
        level = get_effort_level()

    # Forced tier levels bypass scoring entirely
    if level in _FORCED_TIERS:
        return EffortOverride(
            original_score=score,
            adjusted_score=score,
            forced_tier=_FORCED_TIERS[level],
            level=level,
        )

    # Adjustment levels modify the score
    adjustment = _SCORE_ADJUSTMENTS.get(level, 0.0)
    adjusted = min(1.0, max(0.0, score + adjustment))

    return EffortOverride(
        original_score=score,
        adjusted_score=adjusted,
        forced_tier=None,
        level=level,
    )


def is_session_scoped(level: EffortLevel) -> bool:
    """Check if effort level should be session-scoped (not persisted).

    MAX is session-scoped for cost protection — user must re-request each session.
    """
    return level == EffortLevel.MAX
