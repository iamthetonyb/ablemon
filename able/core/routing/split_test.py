"""
Split Testing — A/B testing framework for routing changes.

Allows the evolution daemon (or operators) to test routing changes
on a subset of traffic before full deployment.

Design:
    - Each test has a name, groups with config overrides, and assignment weights
    - Traffic is split deterministically by hashing request_hash + test_id
    - Results are tracked per group with success, latency, cost metrics
    - Tests can be created, paused, concluded, and analyzed
    - Statistical significance via chi-squared test
"""

import hashlib
import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class SplitTest:
    """Definition of a single A/B test."""

    id: str
    name: str
    description: str = ""
    groups: Dict[str, dict] = field(default_factory=dict)
    assignment_weights: Dict[str, float] = field(default_factory=dict)
    status: str = "running"  # "running" | "concluded" | "cancelled"
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    concluded_at: Optional[datetime] = None
    min_samples: int = 100
    results: Optional[Dict[str, dict]] = None

    # Per-group tracking
    _counts: Dict[str, int] = field(default_factory=dict)
    _successes: Dict[str, int] = field(default_factory=dict)
    _total_latency: Dict[str, float] = field(default_factory=dict)
    _total_cost: Dict[str, float] = field(default_factory=dict)


class SplitTestManager:
    """
    A/B testing for routing changes.

    Tests are persisted to YAML and survive restarts.
    Assignment is deterministic via consistent hashing.
    """

    def __init__(
        self,
        config_path: str = "config/split_tests.yaml",
        db_path: str = "data/interaction_log.db",
    ):
        self._config_path = Path(config_path)
        self._db_path = db_path
        self._tests: Dict[str, SplitTest] = {}
        self._load()

    def _load(self):
        """Load tests from disk."""
        if not self._config_path.exists():
            return
        with open(self._config_path) as f:
            data = yaml.safe_load(f) or {}

        for test_data in data.get("tests", []):
            if isinstance(test_data, dict) and "id" in test_data:
                created = test_data.get("created_at")
                if isinstance(created, str):
                    try:
                        created = datetime.fromisoformat(created)
                    except (ValueError, TypeError):
                        created = datetime.now(timezone.utc)
                elif not isinstance(created, datetime):
                    created = datetime.now(timezone.utc)

                concluded = test_data.get("concluded_at")
                if isinstance(concluded, str):
                    try:
                        concluded = datetime.fromisoformat(concluded)
                    except (ValueError, TypeError):
                        concluded = None
                elif not isinstance(concluded, datetime):
                    concluded = None

                test = SplitTest(
                    id=test_data["id"],
                    name=test_data.get("name", ""),
                    description=test_data.get("description", ""),
                    groups=test_data.get("groups", {}),
                    assignment_weights=test_data.get("assignment_weights", {}),
                    status=test_data.get("status", "running"),
                    created_at=created,
                    concluded_at=concluded,
                    min_samples=test_data.get("min_samples", 100),
                    results=test_data.get("results"),
                )
                test._counts = test_data.get("_counts", {})
                test._successes = test_data.get("_successes", {})
                test._total_latency = test_data.get("_total_latency", {})
                test._total_cost = test_data.get("_total_cost", {})
                self._tests[test.id] = test

    def _save(self):
        """Persist tests to disk."""
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        tests_list = []
        for test in self._tests.values():
            tests_list.append({
                "id": test.id,
                "name": test.name,
                "description": test.description,
                "groups": test.groups,
                "assignment_weights": test.assignment_weights,
                "status": test.status,
                "created_at": test.created_at.isoformat()
                if isinstance(test.created_at, datetime)
                else str(test.created_at),
                "concluded_at": test.concluded_at.isoformat()
                if isinstance(test.concluded_at, datetime)
                else test.concluded_at,
                "min_samples": test.min_samples,
                "results": test.results,
                "_counts": test._counts,
                "_successes": test._successes,
                "_total_latency": test._total_latency,
                "_total_cost": test._total_cost,
            })

        data = {
            "tests": tests_list,
            "defaults": {
                "min_samples": 100,
                "max_duration_hours": 168,
                "auto_conclude": True,
            },
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(self._config_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    def create_test(
        self,
        name: str,
        groups: dict,
        weights: Optional[dict] = None,
        min_samples: int = 100,
        description: str = "",
    ) -> SplitTest:
        """
        Create a new split test.

        Args:
            name: Human-readable test name
            groups: Dict mapping group_name -> config overrides
                e.g. {"control": {}, "experiment": {"safety_weight": 0.35}}
            weights: Dict mapping group_name -> probability (must sum to 1.0).
                Defaults to equal split across all groups.
            min_samples: Minimum samples per group before significance check
            description: Optional description

        Returns:
            The created SplitTest
        """
        if not groups or len(groups) < 2:
            raise ValueError("Need at least 2 groups for a split test")

        if weights is None:
            n = len(groups)
            weights = {g: round(1.0 / n, 4) for g in groups}

        if set(weights.keys()) != set(groups.keys()):
            raise ValueError("Weight keys must match group keys")

        total_weight = sum(weights.values())
        if abs(total_weight - 1.0) > 0.01:
            raise ValueError(
                f"Weights must sum to 1.0, got {total_weight}"
            )

        test_id = str(uuid.uuid4())[:8]
        test = SplitTest(
            id=test_id,
            name=name,
            description=description,
            groups=groups,
            assignment_weights=weights,
            status="running",
            min_samples=min_samples,
        )
        # Init tracking dicts for each group
        for g in groups:
            test._counts[g] = 0
            test._successes[g] = 0
            test._total_latency[g] = 0.0
            test._total_cost[g] = 0.0

        self._tests[test_id] = test
        self._save()
        logger.info(f"Split test created: {name} (id={test_id}, groups={list(groups.keys())})")
        return test

    def assign_group(self, test_id: str, request_hash: str) -> str:
        """
        Deterministically assign a request to a group via consistent hashing.

        Same request_hash always maps to the same group for a given test.

        Args:
            test_id: The split test ID
            request_hash: A string identifying the request (session_id, request_id, etc.)

        Returns:
            Group name the request was assigned to

        Raises:
            ValueError: If test_id not found or test not running
        """
        test = self._tests.get(test_id)
        if not test:
            raise ValueError(f"Test '{test_id}' not found")
        if test.status != "running":
            raise ValueError(f"Test '{test_id}' is not running (status={test.status})")

        hash_input = f"{request_hash}:{test_id}"
        hash_val = int(hashlib.sha256(hash_input.encode()).hexdigest(), 16)
        bucket = (hash_val % 10000) / 10000.0

        cumulative = 0.0
        sorted_groups = sorted(test.assignment_weights.items())
        for group_name, weight in sorted_groups:
            cumulative += weight
            if bucket < cumulative:
                return group_name

        # Fallback to last group (float precision edge case)
        return sorted_groups[-1][0]

    def record_outcome(
        self,
        test_id: str,
        group: str,
        success: bool,
        latency_ms: int = 0,
        cost_usd: float = 0.0,
    ):
        """
        Record the outcome for a request in a split test.

        Args:
            test_id: The split test ID
            group: Which group the request was in
            success: Whether the request succeeded
            latency_ms: Response latency in milliseconds
            cost_usd: Cost of the request in USD
        """
        test = self._tests.get(test_id)
        if not test or test.status != "running":
            return

        if group not in test.groups:
            logger.warning(f"Unknown group '{group}' for test '{test_id}'")
            return

        test._counts[group] = test._counts.get(group, 0) + 1
        if success:
            test._successes[group] = test._successes.get(group, 0) + 1
        test._total_latency[group] = (
            test._total_latency.get(group, 0.0) + latency_ms
        )
        test._total_cost[group] = (
            test._total_cost.get(group, 0.0) + cost_usd
        )
        self._save()

    def get_results(self, test_id: str) -> dict:
        """
        Get current results for a test with statistical significance.

        Returns dict with per-group metrics, significance info, and winner.
        """
        test = self._tests.get(test_id)
        if not test:
            raise ValueError(f"Test '{test_id}' not found")

        group_results = {}
        for group_name in test.groups:
            count = test._counts.get(group_name, 0)
            successes = test._successes.get(group_name, 0)
            total_latency = test._total_latency.get(group_name, 0.0)
            total_cost = test._total_cost.get(group_name, 0.0)

            group_results[group_name] = {
                "count": count,
                "successes": successes,
                "failures": count - successes,
                "success_rate_pct": round(
                    successes / count * 100, 2
                ) if count > 0 else 0.0,
                "avg_latency_ms": round(
                    total_latency / count, 1
                ) if count > 0 else 0.0,
                "avg_cost_usd": round(
                    total_cost / count, 6
                ) if count > 0 else 0.0,
                "total_cost_usd": round(total_cost, 4),
            }

        # Check significance between pairs
        group_names = sorted(test.groups.keys())
        significance = {}
        if len(group_names) >= 2:
            a_name, b_name = group_names[0], group_names[1]
            significance = self._check_significance(
                group_results[a_name], group_results[b_name]
            )

        # Determine winner
        winner = self._determine_winner(test, group_results, significance)

        return {
            "id": test.id,
            "name": test.name,
            "description": test.description,
            "status": test.status,
            "created_at": test.created_at.isoformat()
            if isinstance(test.created_at, datetime)
            else str(test.created_at),
            "concluded_at": test.concluded_at.isoformat()
            if isinstance(test.concluded_at, datetime)
            else test.concluded_at,
            "min_samples": test.min_samples,
            "groups": group_results,
            "significance": significance,
            "winner": winner,
        }

    def conclude_test(self, test_id: str) -> dict:
        """
        Conclude a test and return the winning group.

        The test is marked as concluded but not deleted (for audit trail).
        """
        test = self._tests.get(test_id)
        if not test:
            raise ValueError(f"Test '{test_id}' not found")

        test.status = "concluded"
        test.concluded_at = datetime.now(timezone.utc)
        results = self.get_results(test_id)
        test.results = results
        self._save()

        logger.info(
            f"Split test concluded: {test.name} (id={test_id}, winner={results.get('winner')})"
        )
        return results

    def list_tests(self, status: Optional[str] = None) -> List[SplitTest]:
        """
        List all tests, optionally filtered by status.

        Args:
            status: Filter by status ("running", "concluded", "cancelled").
                None returns all tests.
        """
        if status is None:
            return list(self._tests.values())
        return [t for t in self._tests.values() if t.status == status]

    def cancel_test(self, test_id: str):
        """Cancel a running test."""
        test = self._tests.get(test_id)
        if test and test.status == "running":
            test.status = "cancelled"
            test.concluded_at = datetime.now(timezone.utc)
            self._save()

    def get_all_results(self) -> Dict[str, Any]:
        """Get results for all tests (convenience for dashboard)."""
        return {
            "tests": [
                self.get_results(t.id) for t in self._tests.values()
            ],
            "active_count": sum(
                1 for t in self._tests.values() if t.status == "running"
            ),
        }

    def _check_significance(
        self,
        group_a_results: dict,
        group_b_results: dict,
    ) -> dict:
        """
        Simple chi-squared test for statistical significance.

        Compares success/failure counts between two groups.

        Returns dict with:
            - chi_squared: the test statistic
            - p_value: approximate p-value
            - significant: whether p < 0.05
            - sufficient_data: whether both groups meet min sample threshold
        """
        a_success = group_a_results.get("successes", 0)
        a_failure = group_a_results.get("failures", 0)
        b_success = group_b_results.get("successes", 0)
        b_failure = group_b_results.get("failures", 0)

        a_total = a_success + a_failure
        b_total = b_success + b_failure
        grand_total = a_total + b_total

        if grand_total == 0 or a_total == 0 or b_total == 0:
            return {
                "chi_squared": 0.0,
                "p_value": 1.0,
                "significant": False,
                "sufficient_data": False,
            }

        # Build observed and expected
        total_success = a_success + b_success
        total_failure = a_failure + b_failure

        # Expected frequencies
        e_a_success = a_total * total_success / grand_total
        e_a_failure = a_total * total_failure / grand_total
        e_b_success = b_total * total_success / grand_total
        e_b_failure = b_total * total_failure / grand_total

        # Chi-squared statistic
        chi2 = 0.0
        for observed, expected in [
            (a_success, e_a_success),
            (a_failure, e_a_failure),
            (b_success, e_b_success),
            (b_failure, e_b_failure),
        ]:
            if expected > 0:
                chi2 += (observed - expected) ** 2 / expected

        # Approximate p-value from chi-squared with 1 degree of freedom
        # Using the survival function approximation
        p_value = self._chi2_survival(chi2, df=1)

        return {
            "chi_squared": round(chi2, 4),
            "p_value": round(p_value, 6),
            "significant": p_value < 0.05,
            "sufficient_data": a_total >= 30 and b_total >= 30,
        }

    @staticmethod
    def _chi2_survival(x: float, df: int = 1) -> float:
        """
        Approximate survival function (1 - CDF) for chi-squared distribution.

        Uses the regularized incomplete gamma function approximation.
        For df=1 (our case), P(X > x) = 2 * (1 - Phi(sqrt(x)))
        where Phi is the standard normal CDF.
        """
        if x <= 0:
            return 1.0
        if df == 1:
            # For df=1, chi-squared survival = 2 * normal survival of sqrt(x)
            z = math.sqrt(x)
            # Abramowitz & Stegun approximation for normal CDF
            t = 1.0 / (1.0 + 0.2316419 * z)
            d = 0.3989422804014327  # 1/sqrt(2*pi)
            poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))))
            phi_complement = d * math.exp(-0.5 * z * z) * poly
            return 2.0 * phi_complement
        # General case fallback (rough)
        return math.exp(-x / 2.0)

    def _determine_winner(
        self,
        test: SplitTest,
        group_results: dict,
        significance: dict,
    ) -> str:
        """Determine the winning group based on results and significance."""
        group_names = sorted(test.groups.keys())
        if len(group_names) < 2:
            return "inconclusive"

        # Check minimum samples
        for g in group_names:
            if group_results[g]["count"] < test.min_samples:
                return "inconclusive"

        if not significance.get("significant", False):
            return "inconclusive"

        # Winner = highest success rate
        best_group = max(
            group_names,
            key=lambda g: group_results[g]["success_rate_pct"],
        )
        return best_group

    @property
    def active_tests(self) -> List[SplitTest]:
        """Get all running tests."""
        return [t for t in self._tests.values() if t.status == "running"]
