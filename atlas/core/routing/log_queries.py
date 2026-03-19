"""
Log Query Helpers — Analytical queries for the M2.7 evolution daemon.

These queries power the Collect → Analyze step of the self-evolution cycle.
Each returns structured data the daemon can feed to MiniMax M2.7 for analysis.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple


class LogQueries:
    """
    Analytical queries over the interaction_log table.

    All methods accept an optional `since` datetime to scope queries.
    Default: last 24 hours.
    """

    def __init__(self, db_path: str = "data/interaction_log.db"):
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _default_since(self) -> str:
        """Default time window: 24 hours ago."""
        return (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    # ── Failure Analysis ──────────────────────────────────────

    def get_failures_by_tier(
        self, since: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Failure count and rate per tier.

        Used by evolution daemon to detect if a tier is underperforming.
        """
        since = since or self._default_since()
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT
                    selected_tier,
                    COUNT(*) as total,
                    SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failures,
                    ROUND(
                        CAST(SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS REAL)
                        / MAX(COUNT(*), 1) * 100, 2
                    ) as failure_rate_pct,
                    GROUP_CONCAT(DISTINCT error_type) as error_types
                FROM interaction_log
                WHERE timestamp >= ?
                GROUP BY selected_tier
                ORDER BY selected_tier
                """,
                (since,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Escalation Analysis ───────────────────────────────────

    def get_escalation_rate(
        self, since: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        How often routing decisions are overridden or escalated.

        High escalation rate = scorer is under-routing.
        """
        since = since or self._default_since()
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN escalated = 1 THEN 1 ELSE 0 END) as escalations,
                    SUM(CASE WHEN user_correction = 1 THEN 1 ELSE 0 END) as user_corrections,
                    ROUND(
                        CAST(SUM(CASE WHEN escalated = 1 OR user_correction = 1 THEN 1 ELSE 0 END) AS REAL)
                        / MAX(COUNT(*), 1) * 100, 2
                    ) as override_rate_pct
                FROM interaction_log
                WHERE timestamp >= ?
                """,
                (since,),
            ).fetchone()
            return dict(row)
        finally:
            conn.close()

    # ── Cost Analysis ─────────────────────────────────────────

    def get_cost_by_tier(
        self, since: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Total cost broken down by tier.

        Used to track budget consumption and optimize routing for cost.
        """
        since = since or self._default_since()
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT
                    selected_tier,
                    COUNT(*) as interactions,
                    ROUND(SUM(cost_usd), 4) as total_cost_usd,
                    ROUND(AVG(cost_usd), 6) as avg_cost_usd,
                    SUM(input_tokens) as total_input_tokens,
                    SUM(output_tokens) as total_output_tokens
                FROM interaction_log
                WHERE timestamp >= ?
                GROUP BY selected_tier
                ORDER BY selected_tier
                """,
                (since,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Win/Success Analysis ──────────────────────────────────

    def get_wins_by_tier(
        self, since: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Success rate and average latency per tier.

        "Wins" = successful completions without fallback or escalation.
        """
        since = since or self._default_since()
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT
                    selected_tier,
                    COUNT(*) as total,
                    SUM(CASE WHEN success = 1 AND fallback_used = 0
                         AND escalated = 0 THEN 1 ELSE 0 END) as clean_wins,
                    ROUND(
                        CAST(SUM(CASE WHEN success = 1 AND fallback_used = 0
                             AND escalated = 0 THEN 1 ELSE 0 END) AS REAL)
                        / MAX(COUNT(*), 1) * 100, 2
                    ) as clean_win_rate_pct,
                    ROUND(AVG(latency_ms), 1) as avg_latency_ms
                FROM interaction_log
                WHERE timestamp >= ?
                GROUP BY selected_tier
                ORDER BY selected_tier
                """,
                (since,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Domain Accuracy ───────────────────────────────────────

    def get_domain_accuracy(
        self, since: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Success/failure/escalation breakdown by detected domain.

        Helps tune domain-specific weight adjustments.
        """
        since = since or self._default_since()
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT
                    domain,
                    COUNT(*) as total,
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes,
                    SUM(CASE WHEN escalated = 1 THEN 1 ELSE 0 END) as escalations,
                    SUM(CASE WHEN user_correction = 1 THEN 1 ELSE 0 END) as corrections,
                    ROUND(AVG(complexity_score), 3) as avg_score,
                    ROUND(AVG(latency_ms), 1) as avg_latency_ms
                FROM interaction_log
                WHERE timestamp >= ?
                GROUP BY domain
                ORDER BY total DESC
                """,
                (since,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Scoring Drift Detection ───────────────────────────────

    def get_scoring_drift(
        self, since: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Score distribution per scorer version.

        If the average score shifts significantly between versions,
        the evolution daemon should investigate.
        """
        since = since or self._default_since()
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT
                    scorer_version,
                    COUNT(*) as interactions,
                    ROUND(AVG(complexity_score), 3) as avg_score,
                    ROUND(MIN(complexity_score), 3) as min_score,
                    ROUND(MAX(complexity_score), 3) as max_score,
                    ROUND(AVG(CASE WHEN success = 1 THEN 1.0 ELSE 0.0 END) * 100, 2)
                        as success_rate_pct,
                    ROUND(AVG(CASE WHEN escalated = 1 THEN 1.0 ELSE 0.0 END) * 100, 2)
                        as escalation_rate_pct
                FROM interaction_log
                WHERE timestamp >= ?
                GROUP BY scorer_version
                ORDER BY scorer_version
                """,
                (since,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Fallback Analysis ─────────────────────────────────────

    def get_fallback_frequency(
        self, since: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        How often fallbacks are triggered, and which provider chains are used.

        High fallback rate for a provider = reliability issue.
        """
        since = since or self._default_since()
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT
                    selected_provider,
                    COUNT(*) as total,
                    SUM(CASE WHEN fallback_used = 1 THEN 1 ELSE 0 END) as fallbacks,
                    ROUND(
                        CAST(SUM(CASE WHEN fallback_used = 1 THEN 1 ELSE 0 END) AS REAL)
                        / MAX(COUNT(*), 1) * 100, 2
                    ) as fallback_rate_pct,
                    GROUP_CONCAT(DISTINCT actual_provider) as actual_providers_used
                FROM interaction_log
                WHERE timestamp >= ?
                GROUP BY selected_provider
                ORDER BY fallbacks DESC
                """,
                (since,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Summary for Evolution Daemon ──────────────────────────

    def get_evolution_summary(
        self, since: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Single-call summary with all metrics the evolution daemon needs.

        This is the primary entry point for the M2.7 Collect step.
        """
        since = since or self._default_since()
        return {
            "period_start": since,
            "failures_by_tier": self.get_failures_by_tier(since),
            "escalation_rate": self.get_escalation_rate(since),
            "cost_by_tier": self.get_cost_by_tier(since),
            "wins_by_tier": self.get_wins_by_tier(since),
            "domain_accuracy": self.get_domain_accuracy(since),
            "scoring_drift": self.get_scoring_drift(since),
            "fallback_frequency": self.get_fallback_frequency(since),
        }
