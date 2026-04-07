"""
Shared interaction log query helpers for metrics endpoints.

Used by both WebhookServer and the Gateway health server to avoid
duplicating SQLite query logic.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "data/interaction_log.db"


def since_iso(hours: int) -> str:
    """Return ISO timestamp for N hours ago (UTC)."""
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def db_query(sql: str, params: tuple = (), db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """Run a SELECT query against interaction_log.db. Returns list of row dicts."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception as e:
        logger.debug("Metrics query failed: %s", e)
        return []


def db_query_one(sql: str, params: tuple = (), db_path: str = DEFAULT_DB_PATH) -> Dict[str, Any]:
    """Run a SELECT query, return first row dict or empty dict."""
    results = db_query(sql, params, db_path)
    return results[0] if results else {}


def get_metrics_summary(hours: int = 24, db_path: str = DEFAULT_DB_PATH) -> Dict[str, Any]:
    """Overall metrics summary for the given period."""
    since = since_iso(hours)

    totals = db_query_one(
        """SELECT COUNT(*) as total_interactions,
                  SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes,
                  SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failures,
                  ROUND(SUM(cost_usd), 4) as total_cost_usd,
                  ROUND(AVG(latency_ms), 1) as avg_latency_ms,
                  SUM(input_tokens) as total_input_tokens,
                  SUM(output_tokens) as total_output_tokens
           FROM interaction_log WHERE timestamp >= ?""",
        (since,), db_path,
    )

    total = totals.get("total_interactions", 0) or 0
    failures = totals.get("failures", 0) or 0
    success_rate = round((total - failures) / total * 100, 2) if total > 0 else 0.0

    quality = db_query_one(
        """SELECT ROUND(AVG(quality_score), 4) as avg_quality,
                  SUM(CASE WHEN corpus_eligible = 1 THEN 1 ELSE 0 END) as corpus_eligible_count
           FROM interaction_log WHERE timestamp >= ? AND quality_score IS NOT NULL""",
        (since,), db_path,
    )

    return {
        "period_hours": hours,
        "total_interactions": total,
        "success_rate_pct": success_rate,
        "total_cost_usd": totals.get("total_cost_usd", 0) or 0,
        "avg_latency_ms": totals.get("avg_latency_ms", 0) or 0,
        "total_tokens": (totals.get("total_input_tokens", 0) or 0)
                      + (totals.get("total_output_tokens", 0) or 0),
        "avg_quality_score": quality.get("avg_quality") if quality else None,
        "corpus_eligible_count": quality.get("corpus_eligible_count", 0) if quality else 0,
        "phoenix_dashboard": "http://localhost:6006",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def get_routing_metrics(hours: int = 24, db_path: str = DEFAULT_DB_PATH) -> Dict[str, Any]:
    """Per-tier routing breakdown."""
    since = since_iso(hours)

    tiers = db_query(
        """SELECT selected_tier,
                  COUNT(*) as volume,
                  SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes,
                  ROUND(CAST(SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS REAL)
                        / MAX(COUNT(*), 1) * 100, 2) as success_rate_pct,
                  ROUND(SUM(cost_usd), 4) as total_cost_usd,
                  ROUND(AVG(latency_ms), 1) as avg_latency_ms,
                  ROUND(AVG(complexity_score), 3) as avg_complexity,
                  SUM(CASE WHEN fallback_used = 1 THEN 1 ELSE 0 END) as fallback_count,
                  SUM(CASE WHEN escalated = 1 THEN 1 ELSE 0 END) as escalation_count
           FROM interaction_log WHERE timestamp >= ?
           GROUP BY selected_tier ORDER BY selected_tier""",
        (since,), db_path,
    )

    domains = db_query(
        """SELECT domain, COUNT(*) as count,
                  ROUND(AVG(complexity_score), 3) as avg_score,
                  SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes
           FROM interaction_log WHERE timestamp >= ?
           GROUP BY domain ORDER BY count DESC""",
        (since,), db_path,
    )

    return {
        "period_hours": hours,
        "by_tier": tiers,
        "by_domain": domains,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def get_corpus_metrics(db_path: str = DEFAULT_DB_PATH) -> Dict[str, Any]:
    """Distillation corpus stats."""
    corpus_files = []
    total_pairs = 0
    total_size_bytes = 0

    data_dir = Path("data")
    if data_dir.exists():
        for jsonl_path in sorted(data_dir.glob("distillation_*.jsonl")):
            try:
                size = jsonl_path.stat().st_size
                with open(jsonl_path) as fh:
                    line_count = sum(1 for _ in fh)
                corpus_files.append({
                    "file": jsonl_path.name,
                    "pairs": line_count,
                    "size_bytes": size,
                })
                total_pairs += line_count
                total_size_bytes += size
            except Exception:
                continue

    # Also check distillation store
    store_pairs = 0
    try:
        from able.core.distillation.store import DistillationStore
        store = DistillationStore()
        store_pairs = len(store.get_pairs(limit=100_000))
    except Exception:
        pass

    effective_pairs = max(total_pairs, store_pairs)

    return {
        "total_pairs": effective_pairs,
        "total_size_bytes": total_size_bytes,
        "target_pairs": 100,
        "progress_pct": round(min(effective_pairs / 100 * 100, 100), 1),
        "files": corpus_files,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def get_evolution_metrics(hours: int = 168, db_path: str = DEFAULT_DB_PATH) -> Dict[str, Any]:
    """Evolution daemon history and current weights."""
    import yaml

    cycles = []
    cycle_dir = Path("data/evolution_cycles")
    if cycle_dir.exists():
        for f in sorted(cycle_dir.glob("*.yaml"))[-20:]:
            try:
                with open(f) as fh:
                    cycle_data = yaml.safe_load(fh) or {}
                cycles.append({
                    "file": f.name,
                    "version": cycle_data.get("version"),
                    "updated_at": cycle_data.get("last_updated"),
                    "updated_by": cycle_data.get("updated_by"),
                })
            except Exception:
                continue

    current_weights: Dict[str, Any] = {}
    weights_path = Path("config/scorer_weights.yaml")
    if weights_path.exists():
        try:
            with open(weights_path) as f:
                current_weights = yaml.safe_load(f) or {}
        except Exception:
            pass

    since = since_iso(hours)
    drift = db_query(
        """SELECT scorer_version,
                  COUNT(*) as interactions,
                  ROUND(AVG(complexity_score), 3) as avg_score,
                  ROUND(AVG(CASE WHEN success = 1 THEN 1.0 ELSE 0.0 END) * 100, 2) as success_rate_pct
           FROM interaction_log WHERE timestamp >= ?
           GROUP BY scorer_version ORDER BY scorer_version""",
        (since,), db_path,
    )

    return {
        "period_hours": hours,
        "current_version": current_weights.get("version"),
        "current_weights": current_weights.get("features", {}),
        "domain_adjustments": current_weights.get("domain_adjustments", {}),
        "evolution_cycles": cycles,
        "scorer_version_drift": drift,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def get_budget_metrics(hours: int = 24, db_path: str = DEFAULT_DB_PATH) -> Dict[str, Any]:
    """Spend vs budget caps per tier."""
    import yaml

    since = since_iso(hours)
    cost_by_tier = db_query(
        """SELECT selected_tier,
                  ROUND(SUM(cost_usd), 4) as spent_usd,
                  COUNT(*) as interactions,
                  SUM(input_tokens) as input_tokens,
                  SUM(output_tokens) as output_tokens
           FROM interaction_log WHERE timestamp >= ?
           GROUP BY selected_tier ORDER BY selected_tier""",
        (since,), db_path,
    )

    caps: Dict[str, Any] = {}
    weights_path = Path("config/scorer_weights.yaml")
    if weights_path.exists():
        try:
            with open(weights_path) as f:
                w = yaml.safe_load(f) or {}
            caps = {
                "opus_daily_usd": w.get("opus_daily_budget_usd", 25.0),
                "opus_monthly_usd": w.get("opus_monthly_budget_usd", 150.0),
            }
        except Exception:
            pass

    opus_24h = db_query_one(
        "SELECT ROUND(SUM(cost_usd), 4) as spent FROM interaction_log WHERE selected_tier = 4 AND timestamp >= ?",
        (since_iso(24),), db_path,
    )
    opus_30d = db_query_one(
        "SELECT ROUND(SUM(cost_usd), 4) as spent FROM interaction_log WHERE selected_tier = 4 AND timestamp >= ?",
        (since_iso(24 * 30),), db_path,
    )

    return {
        "period_hours": hours,
        "by_tier": cost_by_tier,
        "budget_caps": caps,
        "opus_spend": {
            "last_24h_usd": opus_24h.get("spent") or 0,
            "last_30d_usd": opus_30d.get("spent") or 0,
            "daily_remaining_usd": round(caps.get("opus_daily_usd", 25.0) - (opus_24h.get("spent") or 0), 4),
            "monthly_remaining_usd": round(caps.get("opus_monthly_usd", 150.0) - (opus_30d.get("spent") or 0), 4),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
