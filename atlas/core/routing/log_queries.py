"""
Log Query Helpers — Analytical queries for the M2.7 evolution daemon.

These queries power the Collect → Analyze step of the self-evolution cycle.
Each returns structured data the daemon can feed to MiniMax M2.7 for analysis.

Also provides standalone helper functions for multi-tenant and distillation
queries (used by the corpus builder and tenant dashboards).
"""

from __future__ import annotations

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
                    COALESCE(SUM(CASE WHEN escalated = 1 THEN 1 ELSE 0 END), 0) as escalations,
                    COALESCE(SUM(CASE WHEN user_correction = 1 THEN 1 ELSE 0 END), 0) as user_corrections,
                    COALESCE(ROUND(
                        CAST(SUM(CASE WHEN escalated = 1 OR user_correction = 1 THEN 1 ELSE 0 END) AS REAL)
                        / MAX(COUNT(*), 1) * 100, 2
                    ), 0.0) as override_rate_pct
                FROM interaction_log
                WHERE timestamp >= ?
                """,
                (since,),
            ).fetchone()
            result = dict(row)
            # Ensure no None values leak through on empty DB
            for key in ("total", "escalations", "user_corrections", "override_rate_pct"):
                if result.get(key) is None:
                    result[key] = 0 if key != "override_rate_pct" else 0.0
            return result
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


# ═════════════════════════════════════════════════════════════
# Standalone query helpers for multi-tenant + distillation use
# ═════════════════════════════════════════════════════════════


def _connect(db_path: str) -> sqlite3.Connection:
    """Open a connection with row-factory for dict-style access."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _tenant_clause(tenant_id: Optional[str]) -> Tuple[str, tuple]:
    """Return (SQL fragment, params) for optional tenant filtering."""
    if tenant_id is not None:
        return " AND tenant_id = ?", (tenant_id,)
    return "", ()


def get_failures_by_tier(
    db_path: str,
    tier: int,
    since: str,
    tenant_id: Optional[str] = None,
) -> list[dict]:
    """Failed interactions for a specific tier since *since*."""
    t_sql, t_params = _tenant_clause(tenant_id)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT *
            FROM interaction_log
            WHERE success = 0
              AND selected_tier = ?
              AND timestamp >= ?
              {t_sql}
            ORDER BY timestamp DESC
            """,
            (tier, since, *t_params),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_escalation_rate(
    db_path: str,
    since: str,
    tenant_id: Optional[str] = None,
) -> float:
    """Fraction of interactions that were escalated or user-corrected."""
    t_sql, t_params = _tenant_clause(tenant_id)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            f"""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN escalated = 1 OR user_correction = 1
                    THEN 1 ELSE 0 END) as overrides
            FROM interaction_log
            WHERE timestamp >= ?
              {t_sql}
            """,
            (since, *t_params),
        ).fetchone()
        total = row["total"]
        if total == 0:
            return 0.0
        return row["overrides"] / total
    finally:
        conn.close()


def get_cost_by_tier(
    db_path: str,
    since: str,
    tenant_id: Optional[str] = None,
) -> dict[int, float]:
    """Total cost (USD) per tier since *since*."""
    t_sql, t_params = _tenant_clause(tenant_id)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT selected_tier, COALESCE(SUM(cost_usd), 0.0) as cost
            FROM interaction_log
            WHERE timestamp >= ?
              {t_sql}
            GROUP BY selected_tier
            ORDER BY selected_tier
            """,
            (since, *t_params),
        ).fetchall()
        return {r["selected_tier"]: r["cost"] for r in rows}
    finally:
        conn.close()


def get_wins_by_tier(
    db_path: str,
    since: str,
    tenant_id: Optional[str] = None,
) -> dict[int, int]:
    """Clean win count (success, no fallback, no escalation) per tier."""
    t_sql, t_params = _tenant_clause(tenant_id)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT selected_tier,
                   SUM(CASE WHEN success = 1 AND fallback_used = 0
                        AND escalated = 0 THEN 1 ELSE 0 END) as wins
            FROM interaction_log
            WHERE timestamp >= ?
              {t_sql}
            GROUP BY selected_tier
            ORDER BY selected_tier
            """,
            (since, *t_params),
        ).fetchall()
        return {r["selected_tier"]: r["wins"] for r in rows}
    finally:
        conn.close()


def get_domain_accuracy(
    db_path: str,
    domain: str,
    tier: int,
    tenant_id: Optional[str] = None,
) -> float:
    """Success rate (0.0-1.0) for a specific domain + tier combination."""
    t_sql, t_params = _tenant_clause(tenant_id)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            f"""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes
            FROM interaction_log
            WHERE domain = ?
              AND selected_tier = ?
              {t_sql}
            """,
            (domain, tier, *t_params),
        ).fetchone()
        total = row["total"]
        if total == 0:
            return 0.0
        return row["successes"] / total
    finally:
        conn.close()


def get_scoring_drift(
    db_path: str,
    since: str,
    tenant_id: Optional[str] = None,
) -> dict:
    """Average complexity score in the first vs second half of *since* window."""
    t_sql, t_params = _tenant_clause(tenant_id)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT complexity_score
            FROM interaction_log
            WHERE timestamp >= ?
              {t_sql}
            ORDER BY timestamp ASC
            """,
            (since, *t_params),
        ).fetchall()
        scores = [r["complexity_score"] for r in rows]
        if not scores:
            return {"first_half_avg": 0.0, "second_half_avg": 0.0, "drift": 0.0}
        mid = len(scores) // 2 or 1
        first = scores[:mid]
        second = scores[mid:]
        first_avg = sum(first) / len(first)
        second_avg = sum(second) / len(second) if second else first_avg
        return {
            "first_half_avg": round(first_avg, 4),
            "second_half_avg": round(second_avg, 4),
            "drift": round(second_avg - first_avg, 4),
        }
    finally:
        conn.close()


def get_corpus_eligible(
    db_path: str,
    since: str,
    tenant_id: Optional[str] = None,
) -> list[dict]:
    """Records marked corpus-eligible (ready for distillation export)."""
    t_sql, t_params = _tenant_clause(tenant_id)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT *
            FROM interaction_log
            WHERE corpus_eligible = 1
              AND timestamp >= ?
              {t_sql}
            ORDER BY timestamp DESC
            """,
            (since, *t_params),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_tenant_summary(
    db_path: str,
    tenant_id: str,
    since: str,
) -> dict:
    """Comprehensive stats for a single tenant."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT
                COUNT(*) as total_interactions,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes,
                SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failures,
                SUM(CASE WHEN escalated = 1 THEN 1 ELSE 0 END) as escalations,
                SUM(CASE WHEN corpus_eligible = 1 THEN 1 ELSE 0 END) as corpus_eligible_count,
                COALESCE(SUM(cost_usd), 0.0) as total_cost_usd,
                COALESCE(SUM(input_tokens), 0) as total_input_tokens,
                COALESCE(SUM(output_tokens), 0) as total_output_tokens,
                ROUND(AVG(latency_ms), 1) as avg_latency_ms
            FROM interaction_log
            WHERE tenant_id = ?
              AND timestamp >= ?
            """,
            (tenant_id, since),
        ).fetchone()
        return dict(row)
    finally:
        conn.close()
