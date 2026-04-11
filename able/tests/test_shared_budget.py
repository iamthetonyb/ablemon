"""Tests for F8 — Shared Subagent Budget.

Covers: basic consumption, exhaustion, child budgets, parent propagation,
freezing, snapshots, reset, thread safety.
"""

import threading
import pytest

from able.core.swarm.budget import (
    BudgetExhausted,
    BudgetSnapshot,
    SharedBudget,
)


@pytest.fixture
def budget():
    return SharedBudget(total=20, label="test")


# ── Basic consumption ───────────────────────────────────────────

class TestBasicConsumption:

    def test_initial_state(self, budget):
        assert budget.total == 20
        assert budget.consumed == 0
        assert budget.remaining == 20
        assert not budget.is_exhausted

    def test_consume_one(self, budget):
        remaining = budget.consume(1)
        assert remaining == 19
        assert budget.consumed == 1

    def test_consume_multiple(self, budget):
        budget.consume(5)
        budget.consume(3)
        assert budget.consumed == 8
        assert budget.remaining == 12

    def test_consume_zero(self, budget):
        remaining = budget.consume(0)
        assert remaining == 20
        assert budget.consumed == 0

    def test_consume_negative(self, budget):
        remaining = budget.consume(-1)
        assert remaining == 20  # Ignored


# ── Exhaustion ──────────────────────────────────────────────────

class TestExhaustion:

    def test_exhaust_exact(self, budget):
        budget.consume(20)
        assert budget.is_exhausted
        assert budget.remaining == 0

    def test_exhaust_over(self, budget):
        budget.consume(25)  # Clamped to total
        assert budget.is_exhausted
        assert budget.consumed == 20  # Clamped

    def test_exhaust_raises_on_next(self, budget):
        budget.consume(20)
        with pytest.raises(BudgetExhausted):
            budget.consume(1)

    def test_exhaust_message(self, budget):
        budget.consume(20)
        with pytest.raises(BudgetExhausted, match="test"):
            budget.consume(1)


# ── Child budgets ───────────────────────────────────────────────

class TestChildBudgets:

    def test_allocate_child(self, budget):
        child = budget.allocate_child(max_iterations=8)
        assert child.total == 8
        assert child.remaining == 8

    def test_child_capped_by_parent(self, budget):
        budget.consume(15)  # 5 remaining
        child = budget.allocate_child(max_iterations=10)
        assert child.total == 5  # Capped to parent remaining

    def test_child_default_uses_remaining(self, budget):
        budget.consume(12)
        child = budget.allocate_child()
        assert child.total == 8

    def test_child_consumes_parent(self, budget):
        child = budget.allocate_child(max_iterations=10)
        child.consume(3)
        assert budget.consumed == 3  # Parent sees it

    def test_multiple_children(self, budget):
        c1 = budget.allocate_child(max_iterations=8, label="c1")
        c2 = budget.allocate_child(max_iterations=8, label="c2")
        c1.consume(3)
        c2.consume(4)
        assert budget.consumed == 7

    def test_child_exhaustion_propagates(self, budget):
        budget.consume(18)  # 2 remaining
        child = budget.allocate_child(max_iterations=5)
        assert child.total == 2
        child.consume(2)
        assert child.is_exhausted

    def test_parent_exhaustion_freezes_child(self, budget):
        child = budget.allocate_child(max_iterations=15)
        budget.consume(20)  # Exhaust parent directly
        # Child should raise because parent is exhausted
        with pytest.raises(BudgetExhausted):
            child.consume(1)

    def test_cannot_allocate_when_exhausted(self, budget):
        budget.consume(20)
        with pytest.raises(BudgetExhausted):
            budget.allocate_child()


# ── Freezing ────────────────────────────────────────────────────

class TestFreezing:

    def test_freeze_prevents_consumption(self, budget):
        budget.freeze()
        with pytest.raises(BudgetExhausted, match="frozen"):
            budget.consume(1)

    def test_freeze_cascades_to_children(self, budget):
        child = budget.allocate_child(max_iterations=10)
        budget.freeze()
        with pytest.raises(BudgetExhausted, match="frozen"):
            child.consume(1)


# ── Snapshots ───────────────────────────────────────────────────

class TestSnapshots:

    def test_snapshot(self, budget):
        child = budget.allocate_child(max_iterations=8)
        child.consume(3)
        snap = budget.snapshot()
        assert isinstance(snap, BudgetSnapshot)
        assert snap.total == 20
        assert snap.consumed == 3
        assert snap.remaining == 17
        assert snap.children_active == 1
        assert snap.children_total_consumed == 3

    def test_snapshot_exhausted_child(self, budget):
        child = budget.allocate_child(max_iterations=5)
        child.consume(5)
        snap = budget.snapshot()
        assert snap.children_active == 0  # Child exhausted


# ── Reset ───────────────────────────────────────────────────────

class TestReset:

    def test_reset(self, budget):
        budget.consume(10)
        budget.allocate_child(max_iterations=5)
        budget.reset()
        assert budget.consumed == 0
        assert budget.remaining == 20
        snap = budget.snapshot()
        assert snap.children_active == 0


# ── Thread safety ───────────────────────────────────────────────

class TestThreadSafety:

    def test_concurrent_consumption(self):
        budget = SharedBudget(total=1000, label="thread-test")
        errors = []

        def consume_chunk():
            try:
                for _ in range(100):
                    budget.consume(1)
            except BudgetExhausted:
                pass
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=consume_chunk) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert budget.consumed == 1000
