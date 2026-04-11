"""Tests for D6 — Storm AI Terminal Widgets.

Covers: widget instantiation, rendering, state tracking, cost calculation.
"""

import pytest
from rich.console import Console

from able.cli.widgets import (
    ApprovalPrompt,
    OperationTree,
    CostTracker,
    ContextWindow,
)


# ── ApprovalPrompt ────────────────────────────────────────────


class TestApprovalPrompt:

    def test_instantiation(self):
        ap = ApprovalPrompt(console=Console(file=__import__("io").StringIO()))
        assert ap is not None

    def test_render_risk_levels(self):
        ap = ApprovalPrompt(console=Console(file=__import__("io").StringIO()))
        for level in ("low", "medium", "high", "critical"):
            ap.render("test action", level)


# ── OperationTree ─────────────────────────────────────────────


class TestOperationTree:

    def test_add_operation(self):
        tree = OperationTree(console=Console(file=__import__("io").StringIO()))
        op_id = tree.add("Running test")
        assert op_id is not None

    def test_complete_operation(self):
        tree = OperationTree(console=Console(file=__import__("io").StringIO()))
        op_id = tree.add("Running")
        tree.complete(op_id)

    def test_fail_operation(self):
        tree = OperationTree(console=Console(file=__import__("io").StringIO()))
        op_id = tree.add("Running")
        tree.fail(op_id, "timeout")

    def test_build_tree(self):
        tree = OperationTree(console=Console(file=__import__("io").StringIO()))
        op_id = tree.add("Test operation")
        tree.complete(op_id)
        rendered = tree._build()
        assert rendered is not None

    def test_print(self):
        tree = OperationTree(console=Console(file=__import__("io").StringIO()))
        tree.add("A")
        tree.print()


# ── CostTracker ───────────────────────────────────────────────


class TestCostTracker:

    def test_record_tokens(self):
        ct = CostTracker(console=Console(file=__import__("io").StringIO()))
        ct.record(tier="T4 Opus", input_tokens=1000, output_tokens=500)

    def test_accumulation(self):
        ct = CostTracker(console=Console(file=__import__("io").StringIO()))
        ct.record(tier="T4 Opus", input_tokens=1000, output_tokens=500)
        ct.record(tier="T4 Opus", input_tokens=2000, output_tokens=1000)

    def test_total_cost(self):
        ct = CostTracker(console=Console(file=__import__("io").StringIO()))
        ct.record(tier="T4 Opus", input_tokens=1_000_000, output_tokens=0)
        cost = ct.total_cost()
        assert cost > 0

    def test_free_tiers(self):
        ct = CostTracker(console=Console(file=__import__("io").StringIO()))
        ct.record(tier="T1 Mini", input_tokens=1000, output_tokens=1000)
        assert ct.total_cost() == 0.0

    def test_build_table(self):
        ct = CostTracker(console=Console(file=__import__("io").StringIO()))
        ct.record(tier="T4 Opus", input_tokens=1000, output_tokens=500)
        table = ct.build_table()
        assert table is not None

    def test_print(self):
        ct = CostTracker(console=Console(file=__import__("io").StringIO()))
        ct.record(tier="T4 Opus", input_tokens=1000, output_tokens=500)
        ct.print()


# ── ContextWindow ─────────────────────────────────────────────


class TestContextWindow:

    def test_instantiation(self):
        cw = ContextWindow(max_tokens=200000)
        assert cw.max_tokens == 200000

    def test_update(self):
        cw = ContextWindow(max_tokens=200000)
        cw.update(used=50000, cached=20000)
        assert cw.used == 50000
        assert cw.cached == 20000

    def test_pct_used(self):
        cw = ContextWindow(max_tokens=200000)
        cw.update(used=100000, cached=0)
        assert cw._pct_used == pytest.approx(0.5)

    def test_build_panel(self):
        cw = ContextWindow(max_tokens=200000)
        cw.update(used=160000, cached=40000)
        panel = cw.build()
        assert panel is not None

    def test_print(self):
        cw = ContextWindow(
            max_tokens=200000,
            console=Console(file=__import__("io").StringIO()),
        )
        cw.update(used=180000, cached=0)
        cw.print()
