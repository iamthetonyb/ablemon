"""
Millicent-based budget tracking (Claurst pattern).

Uses integer millicents (1/100,000 USD) to avoid floating-point
accumulation errors. Tracks per-session and per-tier spending.

When budget is exceeded, auto-downgrades to cheaper tier instead
of hard-stopping — graceful degradation over failure.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Optional


# 1 millicent = 1/100,000 USD = $0.00001
MILLICENTS_PER_USD = 100_000


@dataclass
class BudgetEntry:
    """A single cost event."""
    tier: int
    input_tokens: int
    output_tokens: int
    cost_millicents: int
    timestamp: float = field(default_factory=time.time)


@dataclass
class BudgetStatus:
    """Current budget state."""
    spent_millicents: int
    budget_millicents: int  # 0 = unlimited
    remaining_millicents: int
    exceeded: bool
    tier_breakdown: dict[int, int]  # tier -> millicents

    @property
    def spent_usd(self) -> float:
        return self.spent_millicents / MILLICENTS_PER_USD

    @property
    def budget_usd(self) -> float:
        return self.budget_millicents / MILLICENTS_PER_USD

    @property
    def remaining_usd(self) -> float:
        return self.remaining_millicents / MILLICENTS_PER_USD


# Per-tier pricing in USD per million tokens
# (input_usd_per_m, output_usd_per_m)
_TIER_RATES_USD: dict[int, tuple[float, float]] = {
    1: (0.0, 0.0),       # T1: subscription / free
    2: (0.0, 0.0),       # T2: subscription / free
    3: (0.30, 1.20),     # T3: MiniMax M2.7
    4: (15.0, 75.0),     # T4: Opus
    5: (0.0, 0.0),       # T5: local / free
}


class BudgetTracker:
    """Track per-session spending in millicents.

    Usage::

        tracker = BudgetTracker(max_budget_usd=5.0)
        tracker.record(tier=4, input_tokens=10000, output_tokens=5000)
        status = tracker.status()
        if status.exceeded:
            print(f"Budget exceeded! Spent ${status.spent_usd:.4f}")
    """

    def __init__(self, max_budget_usd: float = 0.0) -> None:
        """Initialize tracker.

        Args:
            max_budget_usd: Maximum budget in USD. 0 = unlimited.
        """
        # Read from env if not explicitly set
        if max_budget_usd <= 0:
            env_val = os.environ.get("ABLE_MAX_BUDGET_USD", "0")
            try:
                max_budget_usd = float(env_val)
            except ValueError:
                max_budget_usd = 0.0

        self._budget_millicents = int(max_budget_usd * MILLICENTS_PER_USD)
        self._entries: list[BudgetEntry] = []
        self._total_millicents: int = 0

    def record(self, tier: int, input_tokens: int,
               output_tokens: int) -> BudgetEntry:
        """Record a cost event.

        Args:
            tier: ABLE routing tier (1-5)
            input_tokens: Input token count
            output_tokens: Output token count

        Returns:
            The recorded BudgetEntry with computed cost.
        """
        rates = _TIER_RATES_USD.get(tier, (0.0, 0.0))
        # Cost in millicents = (tokens / 1M) * rate_usd_per_M * millicents_per_usd
        input_cost = int((input_tokens / 1_000_000) * rates[0] * MILLICENTS_PER_USD)
        output_cost = int((output_tokens / 1_000_000) * rates[1] * MILLICENTS_PER_USD)
        total = input_cost + output_cost

        entry = BudgetEntry(
            tier=tier,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_millicents=total,
        )
        self._entries.append(entry)
        self._total_millicents += total
        return entry

    def status(self) -> BudgetStatus:
        """Get current budget status."""
        tier_breakdown: dict[int, int] = {}
        for entry in self._entries:
            tier_breakdown[entry.tier] = (
                tier_breakdown.get(entry.tier, 0) + entry.cost_millicents
            )

        remaining = (
            self._budget_millicents - self._total_millicents
            if self._budget_millicents > 0
            else MILLICENTS_PER_USD * 999  # effectively unlimited
        )

        return BudgetStatus(
            spent_millicents=self._total_millicents,
            budget_millicents=self._budget_millicents,
            remaining_millicents=max(0, remaining),
            exceeded=(
                self._budget_millicents > 0
                and self._total_millicents > self._budget_millicents
            ),
            tier_breakdown=tier_breakdown,
        )

    def suggest_downgrade(self, requested_tier: int) -> Optional[int]:
        """Suggest a cheaper tier if budget is nearly exhausted.

        Returns None if no downgrade needed, or the suggested tier.
        Called before routing to prevent budget blow-through.
        """
        if self._budget_millicents <= 0:
            return None  # No budget, no downgrade

        remaining = self._budget_millicents - self._total_millicents

        # If >20% budget remaining, no downgrade
        if remaining > self._budget_millicents * 0.2:
            return None

        # If <20% remaining, suggest cheaper tier
        if requested_tier == 4 and remaining > 0:
            return 2  # Downgrade Opus → GPT (subscription)
        if requested_tier == 3 and remaining <= 0:
            return 5  # Downgrade MiniMax → local

        if remaining <= 0:
            return 5  # Force local when budget exhausted

        return None

    def reset(self) -> None:
        """Reset tracking (new session)."""
        self._entries.clear()
        self._total_millicents = 0
