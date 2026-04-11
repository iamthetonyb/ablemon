"""
F8 — Shared Subagent Budget.

Parent spawns subagent with `remaining_budget` as `max_iterations`.
Subagent tool calls count against parent total. Prevents runaway chains.

Usage:
    budget = SharedBudget(total=20)
    budget.consume(3)          # Subagent used 3 iterations
    budget.remaining           # 17
    budget.is_exhausted        # False

    # With child budgets:
    child = budget.allocate_child(max_iterations=8)
    child.consume(5)
    budget.remaining           # 12 (parent tracks child consumption)
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class BudgetExhausted(RuntimeError):
    """Raised when a shared budget is fully consumed."""
    pass


@dataclass
class BudgetSnapshot:
    """Point-in-time budget state."""
    total: int
    consumed: int
    remaining: int
    children_active: int
    children_total_consumed: int


class SharedBudget:
    """Thread-safe shared iteration budget.

    A parent task creates a SharedBudget with a total iteration count.
    When spawning subagents, it allocates child budgets that draw from
    the same pool. Any consumption by a child is reflected in the parent.

    Thread-safe: multiple async subagents may consume concurrently.
    """

    def __init__(
        self,
        total: int,
        parent: Optional[SharedBudget] = None,
        label: str = "root",
    ):
        """
        Args:
            total: Maximum iterations this budget allows.
            parent: Optional parent budget (for child budgets).
            label: Human-readable label for logging.
        """
        self._total = total
        self._consumed = 0
        self._parent = parent
        self._label = label
        self._lock = threading.Lock()
        self._children: List[SharedBudget] = []
        self._frozen = False

    @property
    def total(self) -> int:
        return self._total

    @property
    def consumed(self) -> int:
        with self._lock:
            return self._consumed

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, self._total - self._consumed)

    @property
    def is_exhausted(self) -> bool:
        with self._lock:
            return self._consumed >= self._total

    def consume(self, n: int = 1) -> int:
        """Consume n iterations from this budget.

        Args:
            n: Number of iterations to consume.

        Returns:
            Remaining iterations after consumption.

        Raises:
            BudgetExhausted: If budget is already exhausted.
        """
        if n <= 0:
            return self.remaining

        with self._lock:
            if self._frozen:
                raise BudgetExhausted(
                    f"Budget '{self._label}' is frozen"
                )
            if self._consumed >= self._total:
                raise BudgetExhausted(
                    f"Budget '{self._label}' exhausted "
                    f"({self._consumed}/{self._total})"
                )

            self._consumed = min(self._consumed + n, self._total)
            remaining = self._total - self._consumed

        # Propagate to parent
        if self._parent is not None:
            try:
                self._parent.consume(n)
            except BudgetExhausted:
                # Parent exhausted — freeze this child too
                with self._lock:
                    self._frozen = True
                raise

        if remaining <= 0:
            logger.warning("Budget '%s' exhausted (%d/%d)",
                           self._label, self._consumed, self._total)

        return remaining

    def allocate_child(
        self,
        max_iterations: Optional[int] = None,
        label: str = "",
    ) -> SharedBudget:
        """Allocate a child budget that draws from this pool.

        Args:
            max_iterations: Cap for the child. If None, uses remaining.
            label: Label for the child budget.

        Returns:
            A new SharedBudget linked to this parent.
        """
        with self._lock:
            avail = self._total - self._consumed
            if avail <= 0:
                raise BudgetExhausted(
                    f"Cannot allocate child — '{self._label}' exhausted"
                )

        cap = min(max_iterations, avail) if max_iterations else avail
        child_label = label or f"{self._label}.child-{len(self._children)}"

        child = SharedBudget(
            total=cap,
            parent=self,
            label=child_label,
        )

        with self._lock:
            self._children.append(child)

        logger.debug("Allocated child budget '%s' with %d iterations",
                     child_label, cap)
        return child

    def freeze(self) -> None:
        """Freeze this budget — no more consumption allowed."""
        with self._lock:
            self._frozen = True
        for child in self._children:
            child.freeze()

    def snapshot(self) -> BudgetSnapshot:
        """Point-in-time snapshot of budget state."""
        with self._lock:
            children_consumed = sum(c.consumed for c in self._children)
            return BudgetSnapshot(
                total=self._total,
                consumed=self._consumed,
                remaining=max(0, self._total - self._consumed),
                children_active=len([
                    c for c in self._children if not c.is_exhausted
                ]),
                children_total_consumed=children_consumed,
            )

    def reset(self) -> None:
        """Reset budget (for new turn/session)."""
        with self._lock:
            self._consumed = 0
            self._frozen = False
            self._children.clear()
