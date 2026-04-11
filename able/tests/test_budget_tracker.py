"""Tests for millicent-based budget tracking (Claurst pattern)."""

import os
import pytest
from able.core.routing.budget_tracker import (
    BudgetTracker, BudgetEntry, BudgetStatus, MILLICENTS_PER_USD,
)


class TestBudgetTracker:

    def test_default_unlimited(self):
        os.environ.pop("ABLE_MAX_BUDGET_USD", None)
        tracker = BudgetTracker()
        status = tracker.status()
        assert not status.exceeded
        assert status.budget_millicents == 0

    def test_explicit_budget(self):
        tracker = BudgetTracker(max_budget_usd=5.0)
        status = tracker.status()
        assert status.budget_millicents == 5 * MILLICENTS_PER_USD

    def test_record_free_tiers(self):
        tracker = BudgetTracker(max_budget_usd=1.0)
        tracker.record(tier=1, input_tokens=1_000_000, output_tokens=500_000)
        tracker.record(tier=2, input_tokens=1_000_000, output_tokens=500_000)
        tracker.record(tier=5, input_tokens=1_000_000, output_tokens=500_000)
        status = tracker.status()
        assert status.spent_millicents == 0

    def test_record_t4_costs(self):
        tracker = BudgetTracker(max_budget_usd=100.0)
        tracker.record(tier=4, input_tokens=1_000_000, output_tokens=1_000_000)
        status = tracker.status()
        # T4: $15/M input + $75/M output = $90 for 1M each
        assert status.spent_millicents > 0
        assert status.spent_usd == pytest.approx(90.0, rel=0.1)

    def test_budget_exceeded(self):
        tracker = BudgetTracker(max_budget_usd=0.01)
        tracker.record(tier=4, input_tokens=1_000_000, output_tokens=1_000_000)
        status = tracker.status()
        assert status.exceeded

    def test_tier_breakdown(self):
        tracker = BudgetTracker(max_budget_usd=100.0)
        tracker.record(tier=4, input_tokens=100_000, output_tokens=50_000)
        tracker.record(tier=3, input_tokens=100_000, output_tokens=50_000)
        status = tracker.status()
        assert 4 in status.tier_breakdown
        assert 3 in status.tier_breakdown

    def test_remaining_usd(self):
        tracker = BudgetTracker(max_budget_usd=10.0)
        status = tracker.status()
        assert status.remaining_usd == pytest.approx(10.0)

    def test_reset(self):
        tracker = BudgetTracker(max_budget_usd=10.0)
        tracker.record(tier=4, input_tokens=100_000, output_tokens=50_000)
        tracker.reset()
        status = tracker.status()
        assert status.spent_millicents == 0


class TestBudgetDowngrade:

    def test_no_downgrade_when_unlimited(self):
        tracker = BudgetTracker()
        assert tracker.suggest_downgrade(4) is None

    def test_no_downgrade_when_budget_healthy(self):
        tracker = BudgetTracker(max_budget_usd=100.0)
        assert tracker.suggest_downgrade(4) is None

    def test_downgrade_t4_when_low(self):
        tracker = BudgetTracker(max_budget_usd=1.0)
        # Spend ~85% of budget ($15/M input → 55K tokens = ~$0.825)
        tracker.record(tier=4, input_tokens=55_000, output_tokens=0)
        suggestion = tracker.suggest_downgrade(4)
        assert suggestion == 2  # Downgrade to subscription tier

    def test_force_local_when_exhausted(self):
        tracker = BudgetTracker(max_budget_usd=0.001)
        tracker.record(tier=4, input_tokens=1_000_000, output_tokens=1_000_000)
        suggestion = tracker.suggest_downgrade(4)
        assert suggestion == 5  # Force local


class TestBudgetEntry:

    def test_entry_fields(self):
        entry = BudgetEntry(
            tier=4, input_tokens=1000, output_tokens=500,
            cost_millicents=150,
        )
        assert entry.tier == 4
        assert entry.cost_millicents == 150
