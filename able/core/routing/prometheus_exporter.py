"""
Prometheus text format exporter for ABLE metrics.

Converts interaction_log.db data into Prometheus exposition format.
Served at /metrics/prometheus on the gateway.

Metrics exported:
  - able_interactions_total (counter by tier, domain, success)
  - able_cost_usd_total (counter by tier)
  - able_latency_ms (summary by tier)
  - able_tokens_total (counter by direction)
  - able_provider_health (gauge per provider: 1=healthy, 0=unhealthy)
  - able_corpus_pairs_total (gauge)
  - able_scorer_version (gauge)
  - able_budget_remaining_usd (gauge by tier)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from able.core.routing.metrics_queries import (
    db_query,
    db_query_one,
    since_iso,
    DEFAULT_DB_PATH,
)

logger = logging.getLogger(__name__)

# Cache to avoid hammering SQLite on every scrape
_cache: Dict[str, Any] = {"text": "", "expires": 0}
_CACHE_TTL_SECONDS = 15


def _prom_line(metric: str, labels: Dict[str, str], value: float) -> str:
    """Format a single Prometheus metric line."""
    if labels:
        label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
        return f"{metric}{{{label_str}}} {value}"
    return f"{metric} {value}"


def _prom_help(metric: str, help_text: str, mtype: str = "gauge") -> str:
    """Format HELP and TYPE declarations."""
    return f"# HELP {metric} {help_text}\n# TYPE {metric} {mtype}"


def export_prometheus(
    db_path: str = DEFAULT_DB_PATH,
    provider_health: Optional[Dict[str, bool]] = None,
) -> str:
    """
    Generate full Prometheus text exposition.

    Args:
        db_path: Path to interaction_log.db
        provider_health: Optional dict of provider_name → healthy bool

    Returns:
        Prometheus text format string ready to serve as text/plain
    """
    now = time.time()
    if _cache["text"] and now < _cache["expires"]:
        return _cache["text"]

    lines: List[str] = []
    since_24h = since_iso(24)

    # ── Interactions total (counter by tier, domain, success) ──────────
    lines.append(_prom_help(
        "able_interactions_total",
        "Total interactions by tier, domain, and outcome",
        "counter",
    ))
    rows = db_query(
        """SELECT selected_tier, domain,
                  SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as ok,
                  SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as fail
           FROM interaction_log WHERE timestamp >= ?
           GROUP BY selected_tier, domain""",
        (since_24h,), db_path,
    )
    for r in rows:
        tier = str(r.get("selected_tier", 0))
        domain = r.get("domain", "unknown")
        lines.append(_prom_line("able_interactions_total",
                                {"tier": tier, "domain": domain, "status": "success"},
                                r.get("ok", 0) or 0))
        lines.append(_prom_line("able_interactions_total",
                                {"tier": tier, "domain": domain, "status": "failure"},
                                r.get("fail", 0) or 0))

    # ── Cost total (counter by tier) ──────────────────────────────────
    lines.append(_prom_help("able_cost_usd_total", "Total cost in USD by tier", "counter"))
    cost_rows = db_query(
        """SELECT selected_tier, ROUND(SUM(cost_usd), 6) as cost
           FROM interaction_log WHERE timestamp >= ?
           GROUP BY selected_tier""",
        (since_24h,), db_path,
    )
    for r in cost_rows:
        lines.append(_prom_line("able_cost_usd_total",
                                {"tier": str(r.get("selected_tier", 0))},
                                r.get("cost", 0) or 0))

    # ── Latency summary (by tier) ─────────────────────────────────────
    lines.append(_prom_help("able_latency_ms", "Request latency in milliseconds by tier", "summary"))
    latency_rows = db_query(
        """SELECT selected_tier,
                  ROUND(AVG(latency_ms), 1) as avg_ms,
                  COUNT(*) as count,
                  ROUND(SUM(latency_ms), 1) as sum_ms
           FROM interaction_log WHERE timestamp >= ? AND latency_ms > 0
           GROUP BY selected_tier""",
        (since_24h,), db_path,
    )
    for r in latency_rows:
        tier = str(r.get("selected_tier", 0))
        lines.append(_prom_line("able_latency_ms_sum", {"tier": tier},
                                r.get("sum_ms", 0) or 0))
        lines.append(_prom_line("able_latency_ms_count", {"tier": tier},
                                r.get("count", 0) or 0))

    # ── Token totals (counter by direction) ───────────────────────────
    lines.append(_prom_help("able_tokens_total", "Total tokens by direction", "counter"))
    token_row = db_query_one(
        """SELECT SUM(input_tokens) as input_t, SUM(output_tokens) as output_t
           FROM interaction_log WHERE timestamp >= ?""",
        (since_24h,), db_path,
    )
    lines.append(_prom_line("able_tokens_total", {"direction": "input"},
                            token_row.get("input_t", 0) or 0))
    lines.append(_prom_line("able_tokens_total", {"direction": "output"},
                            token_row.get("output_t", 0) or 0))

    # ── Fallback and escalation rates ─────────────────────────────────
    lines.append(_prom_help("able_fallback_total", "Total fallback events", "counter"))
    lines.append(_prom_help("able_escalation_total", "Total escalation events", "counter"))
    fb_row = db_query_one(
        """SELECT SUM(CASE WHEN fallback_used = 1 THEN 1 ELSE 0 END) as fb,
                  SUM(CASE WHEN escalated = 1 THEN 1 ELSE 0 END) as esc
           FROM interaction_log WHERE timestamp >= ?""",
        (since_24h,), db_path,
    )
    lines.append(_prom_line("able_fallback_total", {}, fb_row.get("fb", 0) or 0))
    lines.append(_prom_line("able_escalation_total", {}, fb_row.get("esc", 0) or 0))

    # ── Provider health (gauge per provider) ──────────────────────────
    if provider_health:
        lines.append(_prom_help("able_provider_healthy", "Provider health status (1=healthy, 0=unhealthy)"))
        for name, healthy in provider_health.items():
            lines.append(_prom_line("able_provider_healthy",
                                    {"provider": name},
                                    1 if healthy else 0))

    # ── Corpus pairs (gauge) ──────────────────────────────────────────
    lines.append(_prom_help("able_corpus_pairs_total", "Total distillation corpus pairs"))
    corpus_row = db_query_one(
        "SELECT COUNT(*) as cnt FROM interaction_log WHERE corpus_eligible = 1",
        (), db_path,
    )
    lines.append(_prom_line("able_corpus_pairs_total", {},
                            corpus_row.get("cnt", 0) or 0))

    # ── Scorer version (gauge) ────────────────────────────────────────
    lines.append(_prom_help("able_scorer_version", "Current complexity scorer version"))
    try:
        import yaml
        from pathlib import Path
        weights_path = Path("config/scorer_weights.yaml")
        if weights_path.exists():
            with open(weights_path) as f:
                w = yaml.safe_load(f) or {}
            lines.append(_prom_line("able_scorer_version", {},
                                    w.get("version", 1)))
        else:
            lines.append(_prom_line("able_scorer_version", {}, 1))
    except Exception:
        lines.append(_prom_line("able_scorer_version", {}, 1))

    # ── Budget remaining (gauge by window) ────────────────────────────
    lines.append(_prom_help("able_budget_remaining_usd",
                            "Remaining Opus budget in USD"))
    try:
        import yaml
        from pathlib import Path
        w_path = Path("config/scorer_weights.yaml")
        daily_cap = 25.0
        monthly_cap = 150.0
        if w_path.exists():
            with open(w_path) as f:
                ww = yaml.safe_load(f) or {}
            daily_cap = ww.get("opus_daily_budget_usd", 25.0)
            monthly_cap = ww.get("opus_monthly_budget_usd", 150.0)

        opus_24h = db_query_one(
            "SELECT ROUND(SUM(cost_usd), 4) as spent FROM interaction_log WHERE selected_tier = 4 AND timestamp >= ?",
            (since_iso(24),), db_path,
        )
        opus_30d = db_query_one(
            "SELECT ROUND(SUM(cost_usd), 4) as spent FROM interaction_log WHERE selected_tier = 4 AND timestamp >= ?",
            (since_iso(24 * 30),), db_path,
        )
        lines.append(_prom_line("able_budget_remaining_usd", {"window": "daily"},
                                round(daily_cap - (opus_24h.get("spent") or 0), 4)))
        lines.append(_prom_line("able_budget_remaining_usd", {"window": "monthly"},
                                round(monthly_cap - (opus_30d.get("spent") or 0), 4)))
    except Exception:
        pass

    # ── Build info (gauge) ────────────────────────────────────────────
    lines.append(_prom_help("able_build_info", "ABLE build information"))
    lines.append(_prom_line("able_build_info", {"version": "2.0"}, 1))

    text = "\n".join(lines) + "\n"
    _cache["text"] = text
    _cache["expires"] = now + _CACHE_TTL_SECONDS
    return text
