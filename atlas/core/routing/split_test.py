"""
Split Testing — A/B testing framework for routing changes.

Allows the evolution daemon (or operators) to test routing changes
on a subset of traffic before full deployment.

Design:
    - Each test has a name, control/experiment weights, and config overrides
    - Traffic is split deterministically by hashing session_id + test_name
    - Results are tracked in the interaction log via the `features` field
    - Tests can be created, paused, concluded, and analyzed
    - SQLite storage for durable outcome tracking
    - Statistical significance via chi-squared test (scipy optional, pure-Python fallback)

CLI:
    python -m atlas.core.routing.split_test --start <name> --desc "..." --overrides '{"k": v}'
    python -m atlas.core.routing.split_test --status [name]
    python -m atlas.core.routing.split_test --conclude <name>
"""

import hashlib
import json
import logging
import math
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

DEFAULT_OUTCOMES_DB = "data/split_test_outcomes.db"


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

    @property
    def all_tests(self) -> Dict[str, SplitTest]:
        """Get all tests (any status)."""
        return dict(self._tests)

    def delete_test(self, name: str):
        """Remove a test entirely (for cleanup)."""
        if name in self._tests:
            del self._tests[name]
            self._save()


# ─────────────────────────────────────────────────────────────────────────────
# Statistical Significance
# ─────────────────────────────────────────────────────────────────────────────

def chi_squared_significance(
    ctrl_successes: int,
    ctrl_failures: int,
    exp_successes: int,
    exp_failures: int,
    alpha: float = 0.05,
) -> Dict[str, Any]:
    """
    Chi-squared test for independence on a 2x2 contingency table.

    Uses scipy.stats.chi2 if available, otherwise a pure-Python
    survival function approximation (adequate for 1 degree of freedom).

    Returns dict with chi2 statistic, p_value, significant (bool), and alpha.
    """
    table = [
        [ctrl_successes, ctrl_failures],
        [exp_successes, exp_failures],
    ]
    n = sum(sum(row) for row in table)
    if n == 0:
        return {"chi2": 0.0, "p_value": 1.0, "significant": False, "alpha": alpha}

    row_totals = [sum(row) for row in table]
    col_totals = [table[0][j] + table[1][j] for j in range(2)]

    chi2 = 0.0
    for i in range(2):
        for j in range(2):
            expected = row_totals[i] * col_totals[j] / n
            if expected > 0:
                chi2 += (table[i][j] - expected) ** 2 / expected

    # Compute p-value
    try:
        from scipy.stats import chi2 as chi2_dist
        p_value = 1.0 - chi2_dist.cdf(chi2, df=1)
    except ImportError:
        # Pure-Python approximation for chi2 survival function (df=1)
        # Uses the complementary error function relationship:
        # P(X > x) = erfc(sqrt(x/2)) for df=1
        p_value = math.erfc(math.sqrt(chi2 / 2.0)) if chi2 > 0 else 1.0

    return {
        "chi2": round(chi2, 4),
        "p_value": round(p_value, 6),
        "significant": p_value < alpha,
        "alpha": alpha,
    }


def compute_significance(test: SplitTest, alpha: float = 0.05) -> Dict[str, Any]:
    """
    Compute statistical significance for a split test's success rates.

    Wraps chi_squared_significance with the test's counters.
    """
    ctrl_failures = test.control_count - test.control_successes
    exp_failures = test.experiment_count - test.experiment_successes

    result = chi_squared_significance(
        ctrl_successes=test.control_successes,
        ctrl_failures=ctrl_failures,
        exp_successes=test.experiment_successes,
        exp_failures=exp_failures,
        alpha=alpha,
    )
    result["min_samples_met"] = (
        test.control_count >= 30 and test.experiment_count >= 30
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# SQLite Outcome Storage
# ─────────────────────────────────────────────────────────────────────────────

class SplitTestOutcomeStore:
    """
    Durable SQLite store for individual split-test outcomes.

    The YAML file stores aggregate counters; this table stores
    each individual outcome for post-hoc analysis and audit.
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS split_test_outcomes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        test_name TEXT NOT NULL,
        grp TEXT NOT NULL,
        session_id TEXT,
        success INTEGER NOT NULL DEFAULT 1,
        escalated INTEGER NOT NULL DEFAULT 0,
        cost_usd REAL NOT NULL DEFAULT 0.0,
        latency_ms REAL NOT NULL DEFAULT 0.0,
        recorded_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_sto_test ON split_test_outcomes(test_name);
    CREATE INDEX IF NOT EXISTS idx_sto_grp ON split_test_outcomes(test_name, grp);
    """

    def __init__(self, db_path: str = DEFAULT_OUTCOMES_DB):
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self._db_path)
        try:
            conn.executescript(self.SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.commit()
        finally:
            conn.close()

    def record(
        self,
        test_name: str,
        group: str,
        session_id: str = "",
        success: bool = True,
        escalated: bool = False,
        cost_usd: float = 0.0,
        latency_ms: float = 0.0,
    ):
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                """INSERT INTO split_test_outcomes
                   (test_name, grp, session_id, success, escalated,
                    cost_usd, latency_ms, recorded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    test_name,
                    group,
                    session_id,
                    int(success),
                    int(escalated),
                    cost_usd,
                    latency_ms,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_outcomes(
        self, test_name: str, group: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            if group:
                rows = conn.execute(
                    "SELECT * FROM split_test_outcomes WHERE test_name=? AND grp=?",
                    (test_name, group),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM split_test_outcomes WHERE test_name=?",
                    (test_name,),
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def count(self, test_name: Optional[str] = None) -> int:
        conn = sqlite3.connect(self._db_path)
        try:
            if test_name:
                return conn.execute(
                    "SELECT COUNT(*) FROM split_test_outcomes WHERE test_name=?",
                    (test_name,),
                ).fetchone()[0]
            return conn.execute(
                "SELECT COUNT(*) FROM split_test_outcomes"
            ).fetchone()[0]
        finally:
            conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _cli():
    """CLI entry point for split test management."""
    import argparse

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    parser = argparse.ArgumentParser(
        description="ATLAS Split Test Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Start a test:
    python -m atlas.core.routing.split_test \\
      --start security-weight-bump \\
      --desc "Test safety_critical_weight at 0.35" \\
      --overrides '{"features.safety_critical_weight": 0.35}'

  Check status:
    python -m atlas.core.routing.split_test --status
    python -m atlas.core.routing.split_test --status security-weight-bump

  Conclude a test:
    python -m atlas.core.routing.split_test --conclude security-weight-bump
        """,
    )
    parser.add_argument(
        "--config",
        default="config/split_tests.yaml",
        help="Path to split_tests.yaml (default: config/split_tests.yaml)",
    )
    parser.add_argument("--start", metavar="NAME", help="Start a new split test")
    parser.add_argument("--desc", default="", help="Description for --start")
    parser.add_argument(
        "--overrides",
        default="{}",
        help="JSON dict of experiment overrides for --start",
    )
    parser.add_argument(
        "--control-weight",
        type=float,
        default=0.5,
        help="Control group weight (default: 0.5)",
    )
    parser.add_argument(
        "--status",
        nargs="?",
        const="__all__",
        metavar="NAME",
        help="Show status (optionally for a specific test)",
    )
    parser.add_argument("--conclude", metavar="NAME", help="Conclude a test")

    args = parser.parse_args()
    mgr = SplitTestManager(config_path=args.config)

    if args.start:
        overrides = json.loads(args.overrides)
        exp_weight = round(1.0 - args.control_weight, 4)
        test = mgr.create_test(
            name=args.start,
            description=args.desc,
            control_weight=args.control_weight,
            experiment_weight=exp_weight,
            experiment_overrides=overrides,
        )
        print(f"Created test: {test.name}")
        print(f"  Control weight:    {test.control_weight}")
        print(f"  Experiment weight: {test.experiment_weight}")
        print(f"  Overrides:         {test.experiment_overrides}")
        return

    if args.status is not None:
        if args.status == "__all__":
            results = mgr.get_all_results()
            print(json.dumps(results, indent=2))
        else:
            results = mgr.get_results(args.status)
            test = mgr.all_tests.get(args.status)
            if test:
                results["significance"] = compute_significance(test)
            print(json.dumps(results, indent=2))
        return

    if args.conclude:
        results = mgr.conclude_test(args.conclude)
        test = mgr.all_tests.get(args.conclude)
        if test:
            results["significance"] = compute_significance(test)
        print(json.dumps(results, indent=2))
        return

    parser.print_help()


if __name__ == "__main__":
    _cli()
