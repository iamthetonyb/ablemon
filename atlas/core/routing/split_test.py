"""
Split Testing — A/B testing framework for routing changes.

Allows the evolution daemon (or operators) to test routing changes
on a subset of traffic before full deployment.

Design:
    - Each test has a name, control/experiment weights, and config overrides
    - Traffic is split deterministically by hashing session_id + test_name
    - Results are tracked in the interaction log via the `features` field
    - Tests can be created, paused, concluded, and analyzed
"""

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class SplitTest:
    """Definition of a single A/B test."""

    name: str
    description: str = ""

    # Traffic split (must sum to 1.0)
    control_weight: float = 0.5
    experiment_weight: float = 0.5

    # What the experiment changes (applied on top of current weights)
    experiment_overrides: Dict[str, Any] = field(default_factory=dict)

    # State
    status: str = "active"  # active, paused, concluded
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    concluded_at: Optional[str] = None

    # Counters
    control_count: int = 0
    experiment_count: int = 0
    control_successes: int = 0
    experiment_successes: int = 0
    control_escalations: int = 0
    experiment_escalations: int = 0
    control_total_cost: float = 0.0
    experiment_total_cost: float = 0.0
    control_total_latency: float = 0.0
    experiment_total_latency: float = 0.0


@dataclass
class SplitAssignment:
    """Result of assigning a request to a split test group."""

    test_name: str
    group: str  # "control" or "experiment"
    overrides: Dict[str, Any]  # Empty for control, experiment_overrides for experiment


class SplitTestManager:
    """
    Manages A/B tests for routing configuration changes.

    Tests are persisted to YAML and survive restarts.
    Assignment is deterministic — same session_id always gets same group.
    """

    def __init__(self, config_path: str = "config/split_tests.yaml"):
        self._config_path = Path(config_path)
        self._tests: Dict[str, SplitTest] = {}
        self._load()

    def _load(self):
        """Load tests from disk."""
        if self._config_path.exists():
            with open(self._config_path) as f:
                data = yaml.safe_load(f) or {}
            for name, tdata in data.get("tests", {}).items():
                self._tests[name] = SplitTest(name=name, **tdata)

    def _save(self):
        """Persist tests to disk."""
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"tests": {}}
        for name, test in self._tests.items():
            data["tests"][name] = {
                "description": test.description,
                "control_weight": test.control_weight,
                "experiment_weight": test.experiment_weight,
                "experiment_overrides": test.experiment_overrides,
                "status": test.status,
                "created_at": test.created_at,
                "concluded_at": test.concluded_at,
                "control_count": test.control_count,
                "experiment_count": test.experiment_count,
                "control_successes": test.control_successes,
                "experiment_successes": test.experiment_successes,
                "control_escalations": test.control_escalations,
                "experiment_escalations": test.experiment_escalations,
                "control_total_cost": test.control_total_cost,
                "experiment_total_cost": test.experiment_total_cost,
                "control_total_latency": test.control_total_latency,
                "experiment_total_latency": test.experiment_total_latency,
            }
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        with open(self._config_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    def create_test(
        self,
        name: str,
        description: str = "",
        control_weight: float = 0.5,
        experiment_weight: float = 0.5,
        experiment_overrides: Optional[Dict[str, Any]] = None,
    ) -> SplitTest:
        """
        Create a new split test.

        Args:
            name: Unique test identifier
            experiment_overrides: Dict of weight paths to override values
                e.g. {"features.safety_critical_weight": 0.35}
        """
        if name in self._tests:
            raise ValueError(f"Test '{name}' already exists")

        if abs((control_weight + experiment_weight) - 1.0) > 0.01:
            raise ValueError("Weights must sum to 1.0")

        test = SplitTest(
            name=name,
            description=description,
            control_weight=control_weight,
            experiment_weight=experiment_weight,
            experiment_overrides=experiment_overrides or {},
        )
        self._tests[name] = test
        self._save()
        return test

    def assign(self, session_id: str) -> Optional[SplitAssignment]:
        """
        Assign a session to a split test group.

        Returns None if no active tests.
        Deterministic: same session_id always gets same group.
        """
        active = [t for t in self._tests.values() if t.status == "active"]
        if not active:
            return None

        # Use first active test (single-test simplicity)
        test = active[0]

        # Deterministic hash-based assignment
        hash_input = f"{session_id}:{test.name}"
        hash_val = int(hashlib.sha256(hash_input.encode()).hexdigest(), 16)
        bucket = (hash_val % 1000) / 1000.0

        if bucket < test.control_weight:
            group = "control"
            overrides = {}
        else:
            group = "experiment"
            overrides = test.experiment_overrides

        return SplitAssignment(
            test_name=test.name,
            group=group,
            overrides=overrides,
        )

    def record_outcome(
        self,
        test_name: str,
        group: str,
        success: bool = True,
        escalated: bool = False,
        cost_usd: float = 0.0,
        latency_ms: float = 0.0,
    ):
        """Record the outcome of a routed request for a split test."""
        test = self._tests.get(test_name)
        if not test or test.status != "active":
            return

        if group == "control":
            test.control_count += 1
            if success:
                test.control_successes += 1
            if escalated:
                test.control_escalations += 1
            test.control_total_cost += cost_usd
            test.control_total_latency += latency_ms
        else:
            test.experiment_count += 1
            if success:
                test.experiment_successes += 1
            if escalated:
                test.experiment_escalations += 1
            test.experiment_total_cost += cost_usd
            test.experiment_total_latency += latency_ms

        self._save()

    def conclude_test(self, name: str) -> Dict[str, Any]:
        """
        Conclude a test and return results.

        The test is marked as concluded but not deleted (for audit).
        """
        test = self._tests.get(name)
        if not test:
            raise ValueError(f"Test '{name}' not found")

        test.status = "concluded"
        test.concluded_at = datetime.now(timezone.utc).isoformat()
        self._save()

        return self._compute_results(test)

    def pause_test(self, name: str):
        """Pause an active test."""
        test = self._tests.get(name)
        if test:
            test.status = "paused"
            self._save()

    def resume_test(self, name: str):
        """Resume a paused test."""
        test = self._tests.get(name)
        if test and test.status == "paused":
            test.status = "active"
            self._save()

    def get_results(self, name: str) -> Dict[str, Any]:
        """Get current results for a test."""
        test = self._tests.get(name)
        if not test:
            raise ValueError(f"Test '{name}' not found")
        return self._compute_results(test)

    def get_all_results(self) -> Dict[str, Any]:
        """Get results for all tests."""
        return {
            "tests": [
                self._compute_results(test) for test in self._tests.values()
            ],
            "active_count": sum(
                1 for t in self._tests.values() if t.status == "active"
            ),
        }

    def _compute_results(self, test: SplitTest) -> Dict[str, Any]:
        """Compute comparison metrics for a test."""
        ctrl_rate = (
            test.control_successes / test.control_count * 100
            if test.control_count > 0
            else 0.0
        )
        exp_rate = (
            test.experiment_successes / test.experiment_count * 100
            if test.experiment_count > 0
            else 0.0
        )
        ctrl_esc = (
            test.control_escalations / test.control_count * 100
            if test.control_count > 0
            else 0.0
        )
        exp_esc = (
            test.experiment_escalations / test.experiment_count * 100
            if test.experiment_count > 0
            else 0.0
        )
        ctrl_avg_cost = (
            test.control_total_cost / test.control_count
            if test.control_count > 0
            else 0.0
        )
        exp_avg_cost = (
            test.experiment_total_cost / test.experiment_count
            if test.experiment_count > 0
            else 0.0
        )
        ctrl_avg_latency = (
            test.control_total_latency / test.control_count
            if test.control_count > 0
            else 0.0
        )
        exp_avg_latency = (
            test.experiment_total_latency / test.experiment_count
            if test.experiment_count > 0
            else 0.0
        )

        # Determine winner
        winner = "inconclusive"
        min_samples = 30
        if test.control_count >= min_samples and test.experiment_count >= min_samples:
            if exp_rate > ctrl_rate and exp_esc <= ctrl_esc:
                winner = "experiment"
            elif ctrl_rate > exp_rate:
                winner = "control"
            elif exp_rate == ctrl_rate and exp_avg_cost < ctrl_avg_cost:
                winner = "experiment"

        return {
            "name": test.name,
            "description": test.description,
            "status": test.status,
            "created_at": test.created_at,
            "concluded_at": test.concluded_at,
            "overrides": test.experiment_overrides,
            "control": {
                "count": test.control_count,
                "success_rate_pct": round(ctrl_rate, 2),
                "escalation_rate_pct": round(ctrl_esc, 2),
                "avg_cost_usd": round(ctrl_avg_cost, 6),
                "avg_latency_ms": round(ctrl_avg_latency, 1),
            },
            "experiment": {
                "count": test.experiment_count,
                "success_rate_pct": round(exp_rate, 2),
                "escalation_rate_pct": round(exp_esc, 2),
                "avg_cost_usd": round(exp_avg_cost, 6),
                "avg_latency_ms": round(exp_avg_latency, 1),
            },
            "winner": winner,
        }

    @property
    def active_tests(self) -> List[SplitTest]:
        """Get all active tests."""
        return [t for t in self._tests.values() if t.status == "active"]
