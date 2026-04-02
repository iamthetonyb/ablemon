"""
Metrics Dashboard — JSON metric endpoints for routing observability.

Provides /metrics/* endpoints that can be mounted on the existing
webhook server (aiohttp) or queried programmatically.

Endpoints:
    GET /metrics/health          → System health summary
    GET /metrics/routing         → Routing decision breakdown
    GET /metrics/cost            → Cost analysis by tier
    GET /metrics/evolution       → Evolution daemon status
    GET /metrics/split-tests     → Active split test results
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .log_queries import LogQueries

logger = logging.getLogger(__name__)


class MetricsDashboard:
    """
    Serves routing metrics as JSON.

    Can be used standalone or mounted on the webhook server.
    """

    def __init__(
        self,
        db_path: str = "data/interaction_log.db",
        split_test_manager=None,
        evolution_daemon=None,
    ):
        self._queries = LogQueries(db_path=db_path)
        self._split_tests = split_test_manager
        self._daemon = evolution_daemon

    def get_health(self, hours: int = 24) -> Dict[str, Any]:
        """System health summary."""
        since = self._since(hours)
        failures = self._queries.get_failures_by_tier(since=since)
        escalation = self._queries.get_escalation_rate(since=since)

        total = sum(t.get("total", 0) for t in failures)
        total_failures = sum(t.get("failures", 0) for t in failures)
        failure_rate = (
            round(total_failures / total * 100, 2) if total > 0 else 0.0
        )

        status = "healthy"
        if failure_rate > 20:
            status = "degraded"
        elif failure_rate > 50:
            status = "critical"

        override_rate = escalation.get("override_rate_pct", 0)
        if override_rate > 15 and status == "healthy":
            status = "degraded"

        return {
            "status": status,
            "period_hours": hours,
            "total_interactions": total,
            "failure_rate_pct": failure_rate,
            "override_rate_pct": override_rate,
            "failures_by_tier": failures,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_routing(self, hours: int = 24) -> Dict[str, Any]:
        """Routing decision breakdown."""
        since = self._since(hours)
        return {
            "period_hours": hours,
            "wins_by_tier": self._queries.get_wins_by_tier(since=since),
            "domain_accuracy": self._queries.get_domain_accuracy(since=since),
            "fallback_frequency": self._queries.get_fallback_frequency(since=since),
            "scoring_drift": self._queries.get_scoring_drift(since=since),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_cost(self, hours: int = 24) -> Dict[str, Any]:
        """Cost analysis by tier."""
        since = self._since(hours)
        cost_data = self._queries.get_cost_by_tier(since=since)
        total_cost = sum(t.get("total_cost_usd", 0) for t in cost_data)
        total_tokens = sum(
            t.get("total_input_tokens", 0) + t.get("total_output_tokens", 0)
            for t in cost_data
        )

        return {
            "period_hours": hours,
            "total_cost_usd": round(total_cost, 4),
            "total_tokens": total_tokens,
            "by_tier": cost_data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_evolution(self) -> Dict[str, Any]:
        """Evolution daemon status."""
        if self._daemon:
            return self._daemon.status
        return {"running": False, "message": "Evolution daemon not connected"}

    def get_split_tests(self) -> Dict[str, Any]:
        """Active split test results."""
        if self._split_tests:
            return self._split_tests.get_all_results()
        return {"tests": [], "message": "No split test manager connected"}

    def get_full_dashboard(self, hours: int = 24) -> Dict[str, Any]:
        """Complete dashboard in one call."""
        return {
            "health": self.get_health(hours),
            "routing": self.get_routing(hours),
            "cost": self.get_cost(hours),
            "evolution": self.get_evolution(),
            "split_tests": self.get_split_tests(),
        }

    def _since(self, hours: int) -> str:
        """Generate ISO timestamp for N hours ago."""
        from datetime import timedelta
        return (
            datetime.now(timezone.utc) - timedelta(hours=hours)
        ).isoformat()
