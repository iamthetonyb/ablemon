"""
Metrics Collector — Step 1 of the evolution cycle.

Gathers interaction data from the SQLite log and packages it
for M2.7 analysis. No LLM calls here — pure data aggregation.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

try:
    from able.core.routing.log_queries import LogQueries
except ImportError:
    from able.core.routing.log_queries import LogQueries

logger = logging.getLogger(__name__)


class MetricsCollector:
    """
    Collects and packages interaction metrics for the evolution analyzer.

    Wraps LogQueries with additional context enrichment and
    period management for the daemon's cycle.
    """

    def __init__(self, db_path: str = "data/interaction_log.db", memory=None):
        self._queries = LogQueries(db_path=db_path)
        self._last_collection: Optional[str] = None
        self._submitted_insights: List[Dict[str, Any]] = []
        self._memory = memory

    def collect(
        self, hours: int = 24, since: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Collect all metrics for the evolution cycle.

        Args:
            hours: Lookback window in hours (default 24)
            since: Override start time (ISO format)

        Returns:
            Complete metrics package for the analyzer
        """
        if since is None:
            since = (
                datetime.now(timezone.utc) - timedelta(hours=hours)
            ).isoformat()

        summary = self._queries.get_evolution_summary(since=since)
        proactive_insights = list(self._submitted_insights)
        self._submitted_insights.clear()

        # Enrich with collection metadata
        summary["collection_metadata"] = {
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "lookback_hours": hours,
            "period_start": since,
            "previous_collection": self._last_collection,
        }
        summary["proactive_insights"] = proactive_insights

        # Add computed health indicators
        summary["health_indicators"] = self._compute_health(summary)

        # Enrich with durable memory context (operator learnings, domain signals)
        summary["memory_context"] = self._collect_memory_context(summary)

        # Add compression analysis for evolution daemon
        summary["compression_analysis"] = self._collect_compression_metrics(since)

        self._last_collection = datetime.now(timezone.utc).isoformat()
        return summary

    def _compute_health(self, summary: Dict[str, Any]) -> Dict[str, Any]:
        """
        Compute high-level health indicators from raw metrics.

        These help the analyzer focus on problems.
        """
        health = {
            "overall": "healthy",
            "alerts": [],
        }

        # Check failure rate
        failures = summary.get("failures_by_tier", [])
        for tier_data in failures:
            rate = tier_data.get("failure_rate_pct") or 0
            if rate > 20:
                health["alerts"].append(
                    f"Tier {tier_data['selected_tier']} failure rate: {rate}%"
                )
                health["overall"] = "degraded"
            elif rate > 50:
                health["overall"] = "critical"

        # Check escalation rate
        escalation = summary.get("escalation_rate", {})
        override_rate = escalation.get("override_rate_pct") or 0
        if override_rate > 15:
            health["alerts"].append(
                f"High override rate: {override_rate}% (scorer may be under-routing)"
            )
            if health["overall"] == "healthy":
                health["overall"] = "degraded"

        # Check fallback frequency
        fallbacks = summary.get("fallback_frequency", [])
        for provider_data in fallbacks:
            fb_rate = provider_data.get("fallback_rate_pct") or 0
            if fb_rate > 30:
                health["alerts"].append(
                    f"Provider {provider_data['selected_provider']} fallback rate: {fb_rate}%"
                )

        return health

    def _collect_memory_context(
        self, summary: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Query hybrid memory for durable learnings that should inform
        the evolution cycle.  Returns a lightweight context dict that
        the analyzer can weigh alongside interaction metrics.

        Runs synchronously — HybridMemory.search() is sync — and
        degrades gracefully if memory is unavailable.
        """
        if self._memory is None:
            return {"available": False}

        try:
            from able.memory.hybrid_memory import MemoryType
        except ImportError:
            return {"available": False}

        # Build a query from the domains and alerts the metrics surface
        query_parts = ["routing failures", "escalation patterns"]
        for alert in summary.get("health_indicators", {}).get("alerts", []):
            query_parts.append(alert[:80])
        for domain_row in summary.get("domain_accuracy", [])[:3]:
            domain = domain_row.get("domain") or domain_row.get("name")
            if domain:
                query_parts.append(f"{domain} quality")

        query = "; ".join(query_parts[:5])

        results = self._memory.search(
            query=query,
            memory_types=[MemoryType.LEARNING, MemoryType.SKILL],
            limit=5,
            min_score=0.3,
        )

        learnings = []
        for r in results:
            learnings.append({
                "content": r.entry.content[:300],
                "type": r.entry.memory_type.value,
                "score": round(r.score, 3),
            })

        logger.info(
            "Memory context: %d learnings enriching evolution metrics", len(learnings)
        )

        return {
            "available": True,
            "query": query,
            "learnings": learnings,
        }

    def _collect_compression_metrics(self, since: str) -> Dict[str, Any]:
        """Aggregate compression telemetry for the evolution daemon.

        Returns avg_ratio, total_saved, by_mode breakdown, and saturation flag.
        Used by the analyzer to recommend compression rule changes.
        """
        try:
            from able.core.routing.interaction_log import InteractionLogger
            il = InteractionLogger(db_path=self._queries._db_path)
            stats = il.get_compression_stats(since)
        except Exception:
            logger.debug("Compression metrics unavailable", exc_info=True)
            return {"available": False}

        # Saturation detection: if avg_ratio < 0.25 and quality is high,
        # compression is near-optimal — no further changes needed
        avg_ratio = stats.get("avg_ratio", 1.0)
        saturated = avg_ratio < 0.25 and stats.get("compressed_count", 0) >= 10

        stats["saturated"] = saturated
        stats["available"] = True
        return stats

    @property
    def queries(self) -> LogQueries:
        """Direct access to log queries for ad-hoc analysis."""
        return self._queries

    def submit_insight(
        self,
        title: str,
        description: str,
        *,
        source: str = "proactive",
        category: str = "learning_pattern",
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Queue a proactive insight for the next evolution cycle."""
        insight = {
            "source": source,
            "category": category,
            "title": title,
            "description": description,
            "data": data or {},
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }
        self._submitted_insights.append(insight)
        logger.info("Queued proactive insight for evolution: %s", title)
        return insight
