"""
Memory freshness / staleness warnings (Claurst pattern).

Automatic staleness caveats injected when memories are old, preventing
the agent from treating outdated information as current.

"Today"/"yesterday" = no warning. Older memories get caveats that scale
with age. This prevents the common failure mode where the model asserts
stale facts as ground truth.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class FreshnessResult:
    """Result of a freshness check."""
    is_stale: bool
    age_days: float
    caveat: str  # Empty if fresh


def check_freshness(timestamp: float | datetime | str,
                    stale_after_days: int = 7) -> FreshnessResult:
    """Check memory freshness and return appropriate caveat.

    Args:
        timestamp: Unix timestamp, datetime, or ISO string of when the memory
                   was created/last verified.
        stale_after_days: Number of days after which a memory is considered stale.

    Returns:
        FreshnessResult with is_stale flag and caveat text.
    """
    now = time.time()

    if isinstance(timestamp, str):
        try:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            ts = dt.timestamp()
        except (ValueError, TypeError):
            return FreshnessResult(
                is_stale=True, age_days=-1,
                caveat="[STALE: unknown age — timestamp could not be parsed. Verify before using.]",
            )
    elif isinstance(timestamp, datetime):
        ts = timestamp.timestamp()
    else:
        ts = float(timestamp)

    age_s = now - ts
    age_days = age_s / 86400

    if age_days < 0:
        # Future timestamp, probably fine
        return FreshnessResult(is_stale=False, age_days=0, caveat="")

    if age_days < 1:
        return FreshnessResult(is_stale=False, age_days=age_days, caveat="")

    if age_days < 2:
        return FreshnessResult(is_stale=False, age_days=age_days, caveat="")

    if age_days < stale_after_days:
        return FreshnessResult(
            is_stale=False, age_days=age_days,
            caveat=f"[Memory is {int(age_days)} days old. Verify if time-sensitive.]",
        )

    if age_days < 30:
        return FreshnessResult(
            is_stale=True, age_days=age_days,
            caveat=(
                f"[STALE: {int(age_days)} days old. Memories are point-in-time "
                "observations — claims about code, configs, or state may be "
                "outdated. Verify against current state before asserting as fact.]"
            ),
        )

    if age_days < 90:
        return FreshnessResult(
            is_stale=True, age_days=age_days,
            caveat=(
                f"[STALE: {int(age_days)} days old (~{int(age_days/30)} months). "
                "This memory is likely outdated. Do NOT assert its contents as "
                "current without verification.]"
            ),
        )

    return FreshnessResult(
        is_stale=True, age_days=age_days,
        caveat=(
            f"[STALE: {int(age_days)} days old (~{int(age_days/30)} months). "
            "Archival only — treat as historical context, not current fact.]"
        ),
    )


def annotate_memory(content: str, timestamp: float | datetime | str,
                    stale_after_days: int = 7) -> str:
    """Return memory content with freshness caveat prepended if stale.

    Fresh memories returned unchanged. Stale memories get a warning header.
    """
    result = check_freshness(timestamp, stale_after_days)
    if result.caveat:
        return f"{result.caveat}\n{content}"
    return content
