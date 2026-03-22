"""
ATLAS Webhook Server.

Receives events from external services and routes them to the appropriate handlers.

Endpoints:
    POST /webhook/github    → GitHub push, PR, issue events
    POST /webhook/stripe    → Stripe payment events
    POST /webhook/telegram  → Telegram updates (alternative to polling)
    POST /webhook/custom    → Generic webhook receiver
    GET  /health            → Health check
    GET  /status            → System status dashboard
    GET  /metrics           → JSON summary of current routing metrics
    GET  /metrics/routing   → Per-tier breakdown (success rate, volume, cost, latency)
    GET  /metrics/evolution → Evolution daemon history (cycles, changes, deltas)
    GET  /metrics/budget    → Spend vs budget caps per tier + GPU hours
    GET  /metrics/skills    → Skill usage and effectiveness
    GET  /metrics/corpus    → Distillation corpus stats
    GET  /metrics/tenants   → All tenants overview
    GET  /tenant/{tenant_id}/dashboard → Per-tenant full dashboard

Usage:
    python atlas/tools/webhooks/server.py --port 8080

    Or programmatically:
        from atlas.tools.webhooks import start_server
        await start_server(port=8080)
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

logger = logging.getLogger(__name__)

try:
    from aiohttp import web
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    logger.warning("aiohttp not installed — webhook server unavailable. Run: pip install aiohttp")


@dataclass
class WebhookEvent:
    """A normalized webhook event from any source"""
    source: str                          # "github", "stripe", "telegram", "custom"
    event_type: str                      # e.g. "push", "pull_request", "payment.succeeded"
    payload: Dict[str, Any]             # Raw payload from the source
    headers: Dict[str, str]             # Request headers
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    verified: bool = False               # Whether signature was verified


# ─────────────────────────────────────────────────────────────────────────────
# Signature verification
# ─────────────────────────────────────────────────────────────────────────────

def verify_github_signature(payload_bytes: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub webhook HMAC-SHA256 signature"""
    if not signature or not secret:
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_stripe_signature(payload_bytes: bytes, signature: str, secret: str) -> bool:
    """Verify Stripe webhook signature"""
    if not signature or not secret:
        return False
    try:
        # Stripe uses timestamp+signature format
        parts = {k: v for k, v in (p.split("=", 1) for p in signature.split(","))}
        timestamp = parts.get("t", "")
        sig = parts.get("v1", "")
        signed_payload = f"{timestamp}.{payload_bytes.decode()}"
        expected = hmac.new(
            secret.encode(),
            signed_payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, sig)
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Webhook Server
# ─────────────────────────────────────────────────────────────────────────────

class WebhookServer:
    """
    Lightweight webhook receiver for ATLAS.

    Handles incoming webhooks from external services, verifies signatures,
    normalizes events, and routes to registered handlers.
    """

    def __init__(self, port: int = 8080, host: str = "0.0.0.0"):
        self.port = port
        self.host = host
        self.handlers: Dict[str, List[Callable]] = {
            "github": [],
            "stripe": [],
            "telegram": [],
            "custom": [],
        }
        self.event_log: List[WebhookEvent] = []

        # Secrets from environment
        self.secrets = {
            "github": os.environ.get("GITHUB_WEBHOOK_SECRET", ""),
            "stripe": os.environ.get("STRIPE_WEBHOOK_SECRET", ""),
        }

    def on(self, source: str, handler: Callable):
        """Register a handler for webhook events from a source"""
        if source not in self.handlers:
            self.handlers[source] = []
        self.handlers[source].append(handler)
        logger.info(f"Registered webhook handler for: {source}")

    async def _dispatch(self, event: WebhookEvent):
        """Dispatch event to all registered handlers"""
        self.event_log.append(event)
        handlers = self.handlers.get(event.source, []) + self.handlers.get("custom", [])

        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
            except Exception as e:
                logger.error(f"Webhook handler error ({event.source}): {e}")

    # ── Route handlers ────────────────────────────────────────────────────────

    async def _handle_github(self, request) -> "web.Response":
        body = await request.read()
        signature = request.headers.get("X-Hub-Signature-256", "")
        event_type = request.headers.get("X-GitHub-Event", "unknown")

        verified = verify_github_signature(body, signature, self.secrets["github"])
        if self.secrets["github"] and not verified:
            logger.warning(f"GitHub webhook signature verification failed")
            return web.Response(status=401, text="Signature verification failed")

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return web.Response(status=400, text="Invalid JSON")

        event = WebhookEvent(
            source="github",
            event_type=event_type,
            payload=payload,
            headers=dict(request.headers),
            verified=verified,
        )

        logger.info(f"GitHub webhook: {event_type} on {payload.get('repository', {}).get('full_name', 'unknown')}")
        asyncio.create_task(self._dispatch(event))
        return web.Response(text="OK")

    async def _handle_stripe(self, request) -> "web.Response":
        body = await request.read()
        signature = request.headers.get("Stripe-Signature", "")

        verified = verify_stripe_signature(body, signature, self.secrets["stripe"])
        if self.secrets["stripe"] and not verified:
            logger.warning("Stripe webhook signature verification failed")
            return web.Response(status=401, text="Signature verification failed")

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return web.Response(status=400, text="Invalid JSON")

        event = WebhookEvent(
            source="stripe",
            event_type=payload.get("type", "unknown"),
            payload=payload,
            headers=dict(request.headers),
            verified=verified,
        )

        logger.info(f"Stripe webhook: {event.event_type}")
        asyncio.create_task(self._dispatch(event))
        return web.Response(text="OK")

    async def _handle_telegram(self, request) -> "web.Response":
        try:
            payload = await request.json()
        except Exception:
            return web.Response(status=400, text="Invalid JSON")

        event_type = "message" if "message" in payload else "update"
        event = WebhookEvent(
            source="telegram",
            event_type=event_type,
            payload=payload,
            headers=dict(request.headers),
            verified=True,  # Telegram doesn't sign webhooks
        )

        asyncio.create_task(self._dispatch(event))
        return web.Response(text="OK")

    async def _handle_custom(self, request) -> "web.Response":
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        event_type = request.match_info.get("event_type", "custom")
        event = WebhookEvent(
            source="custom",
            event_type=event_type,
            payload=payload,
            headers=dict(request.headers),
            verified=False,
        )

        logger.info(f"Custom webhook: {event_type}")
        asyncio.create_task(self._dispatch(event))
        return web.Response(text="OK")

    async def _handle_health(self, request) -> "web.Response":
        return web.json_response({
            "status": "healthy",
            "events_received": len(self.event_log),
            "sources": list(self.handlers.keys()),
        })

    async def _handle_status(self, request) -> "web.Response":
        """
        Dashboard status endpoint — returns system health, active tasks,
        recent audit entries, skill stats, and provider chain status.

        Popebot-inspired: provides a lightweight API for agent monitoring.
        """
        atlas_home = Path.home() / ".atlas"
        status = {
            "system": "ATLAS",
            "status": "operational",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "events_received": len(self.event_log),
            "webhook_sources": list(self.handlers.keys()),
        }

        # Active objectives
        objectives_file = atlas_home / "memory" / "current_objectives.yaml"
        if objectives_file.exists():
            try:
                with open(objectives_file) as f:
                    status["objectives"] = yaml.safe_load(f) or {}
            except Exception:
                status["objectives"] = {"error": "failed to load"}
        else:
            status["objectives"] = {}

        # Pending queue count
        queue_file = atlas_home / "queue" / "pending.yaml"
        if queue_file.exists():
            try:
                with open(queue_file) as f:
                    queue_data = yaml.safe_load(f) or {}
                    tasks = queue_data.get("tasks", [])
                    status["pending_tasks"] = len(tasks)
            except Exception:
                status["pending_tasks"] = 0
        else:
            status["pending_tasks"] = 0

        # Audit trail summary
        try:
            from atlas.audit.git_trail import GitAuditTrail
            trail = GitAuditTrail(atlas_home)
            status["audit"] = trail.get_dashboard_summary()
        except Exception:
            status["audit"] = {"error": "audit trail unavailable"}

        # Recent webhook events (last 10)
        status["recent_events"] = [
            {
                "source": e.source,
                "type": e.event_type,
                "time": e.timestamp.isoformat(),
                "verified": e.verified,
            }
            for e in self.event_log[-10:]
        ]

        return web.json_response(status)

    # ── Metrics endpoints ─────────────────────────────────────────────────────

    def _get_interaction_db(self) -> str:
        """Resolve interaction log DB path."""
        return os.environ.get("ATLAS_INTERACTION_DB", "data/interaction_log.db")

    def _safe_query(self, db_path: str, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """Run a read-only query; return empty list if DB missing or error."""
        if not Path(db_path).exists():
            return []
        conn = None
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"DB query error ({db_path}): {e}")
            return []
        finally:
            if conn:
                conn.close()

    def _safe_query_one(self, db_path: str, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        """Run a read-only query; return first row or None."""
        rows = self._safe_query(db_path, sql, params)
        return rows[0] if rows else None

    async def _handle_metrics(self, request) -> "web.Response":
        """GET /metrics — High-level summary of all metrics."""
        db = self._get_interaction_db()

        row = self._safe_query_one(db, """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes,
                COALESCE(SUM(cost_usd), 0.0) as total_cost,
                COALESCE(AVG(latency_ms), 0.0) as avg_latency
            FROM interaction_log
        """)
        total = row["total"] if row else 0
        successes = row["successes"] if row else 0
        total_cost = row["total_cost"] if row else 0.0
        avg_latency = row["avg_latency"] if row else 0.0

        split_tests: Dict[str, Any] = {"active_count": 0}
        try:
            from atlas.core.routing.split_test import SplitTestManager
            mgr = SplitTestManager()
            split_tests = mgr.get_all_results()
        except Exception:
            pass

        return web.json_response({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "interactions": {
                "total": total,
                "successes": successes,
                "success_rate_pct": round(successes / max(total, 1) * 100, 2),
            },
            "cost_usd": round(total_cost, 4),
            "avg_latency_ms": round(avg_latency, 1),
            "split_tests_active": split_tests.get("active_count", 0),
            "events_received": len(self.event_log),
        })

    async def _handle_metrics_routing(self, request) -> "web.Response":
        """GET /metrics/routing — Per-tier breakdown."""
        db = self._get_interaction_db()

        tiers = self._safe_query(db, """
            SELECT
                selected_tier,
                COUNT(*) as volume,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes,
                ROUND(CAST(SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS REAL)
                      / MAX(COUNT(*), 1) * 100, 2) as success_rate_pct,
                ROUND(SUM(cost_usd), 4) as total_cost_usd,
                ROUND(AVG(cost_usd), 6) as avg_cost_usd,
                ROUND(AVG(latency_ms), 1) as avg_latency_ms,
                ROUND(MIN(latency_ms), 1) as min_latency_ms,
                ROUND(MAX(latency_ms), 1) as max_latency_ms,
                SUM(CASE WHEN fallback_used = 1 THEN 1 ELSE 0 END) as fallbacks,
                SUM(CASE WHEN escalated = 1 THEN 1 ELSE 0 END) as escalations
            FROM interaction_log
            GROUP BY selected_tier
            ORDER BY selected_tier
        """)

        providers = self._safe_query(db, """
            SELECT
                selected_provider,
                selected_tier,
                COUNT(*) as volume,
                ROUND(CAST(SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS REAL)
                      / MAX(COUNT(*), 1) * 100, 2) as success_rate_pct,
                ROUND(AVG(latency_ms), 1) as avg_latency_ms
            FROM interaction_log
            GROUP BY selected_provider
            ORDER BY volume DESC
        """)

        return web.json_response({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tiers": tiers,
            "providers": providers,
        })

    async def _handle_metrics_evolution(self, request) -> "web.Response":
        """GET /metrics/evolution — Evolution daemon history."""
        cycle_dir = Path(os.environ.get("ATLAS_EVOLUTION_DIR", "data/evolution_cycles"))
        cycles: List[Dict[str, Any]] = []

        if cycle_dir.exists():

            for fpath in sorted(cycle_dir.glob("*.yaml"))[-20:]:
                try:
                    with open(fpath) as f:
                        cycle_data = yaml.safe_load(f) or {}
                    cycles.append({
                        "file": fpath.name,
                        **cycle_data,
                    })
                except Exception:
                    cycles.append({"file": fpath.name, "error": "parse_failed"})

        weights_path = Path(os.environ.get("ATLAS_WEIGHTS_PATH", "config/scorer_weights.yaml"))
        current_version: Optional[int] = None
        if weights_path.exists():

            try:
                with open(weights_path) as f:
                    w = yaml.safe_load(f) or {}
                current_version = w.get("version")
            except Exception:
                pass

        return web.json_response({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "current_weights_version": current_version,
            "total_cycles": len(cycles),
            "recent_cycles": cycles,
        })

    async def _handle_metrics_budget(self, request) -> "web.Response":
        """GET /metrics/budget — Spend vs budget caps + GPU hours."""
        db = self._get_interaction_db()

        tier_spend = self._safe_query(db, """
            SELECT
                selected_tier,
                ROUND(SUM(cost_usd), 4) as total_usd,
                SUM(input_tokens) as input_tokens,
                SUM(output_tokens) as output_tokens
            FROM interaction_log
            GROUP BY selected_tier
            ORDER BY selected_tier
        """)

        caps: Dict[str, Any] = {}
        config_path = Path(os.environ.get("ATLAS_ROUTING_CONFIG", "config/routing_config.yaml"))
        if config_path.exists():
            try:
                with open(config_path) as f:
                    cfg = yaml.safe_load(f) or {}
                caps = cfg.get("budget", {})
            except Exception:
                pass

        total_spend = sum(t.get("total_usd", 0.0) for t in tier_spend)

        gpu_hours = float(os.environ.get("ATLAS_GPU_HOURS_USED", "0.0"))

        return web.json_response({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tier_spend": tier_spend,
            "total_spend_usd": round(total_spend, 4),
            "budget_caps": caps,
            "gpu_hours_used": gpu_hours,
            "budget_remaining": {
                "opus_daily": round(caps.get("opus_daily_usd", 0) - total_spend, 2),
                "total_monthly": round(caps.get("total_monthly_cap_usd", 0) - total_spend, 2),
            },
        })

    async def _handle_metrics_skills(self, request) -> "web.Response":
        """GET /metrics/skills — Skill usage and effectiveness."""
        db = self._get_interaction_db()

        domain_usage = self._safe_query(db, """
            SELECT
                domain,
                COUNT(*) as invocations,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes,
                ROUND(CAST(SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS REAL)
                      / MAX(COUNT(*), 1) * 100, 2) as success_rate_pct,
                ROUND(AVG(latency_ms), 1) as avg_latency_ms,
                ROUND(SUM(cost_usd), 4) as total_cost_usd
            FROM interaction_log
            GROUP BY domain
            ORDER BY invocations DESC
        """)

        skill_count = 0
        skill_index_path = Path("atlas/skills/SKILL_INDEX.yaml")
        if skill_index_path.exists():
            try:
                with open(skill_index_path) as f:
                    idx = yaml.safe_load(f) or {}
                skills_data = idx.get("skills", [])
                if isinstance(skills_data, (list, dict)):
                    skill_count = len(skills_data)
            except Exception:
                pass

        return web.json_response({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "registered_skills": skill_count,
            "domain_usage": domain_usage,
        })

    async def _handle_metrics_corpus(self, request) -> "web.Response":
        """GET /metrics/corpus — Distillation corpus stats."""
        data_dir = Path(os.environ.get("ATLAS_DATA_DIR", "data"))
        corpus_files = sorted(data_dir.glob("distillation_*.jsonl"))

        total_pairs = 0
        domains: Dict[str, int] = {}
        file_stats: List[Dict[str, Any]] = []

        for fp in corpus_files:
            line_count = 0
            try:
                with open(fp) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        line_count += 1
                        try:
                            obj = json.loads(line)
                            d = obj.get("domain", "unknown")
                            domains[d] = domains.get(d, 0) + 1
                        except json.JSONDecodeError:
                            pass
            except Exception:
                pass
            total_pairs += line_count
            file_stats.append({
                "file": fp.name,
                "pairs": line_count,
                "size_bytes": fp.stat().st_size if fp.exists() else 0,
            })

        target_min = 100
        readiness_pct = min(round(total_pairs / target_min * 100, 1), 100.0)

        return web.json_response({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_pairs": total_pairs,
            "target_min_pairs": target_min,
            "readiness_pct": readiness_pct,
            "domains": domains,
            "files": file_stats,
        })

    async def _handle_metrics_tenants(self, request) -> "web.Response":
        """GET /metrics/tenants — All tenants overview."""
        db = self._get_interaction_db()

        tenants = self._safe_query(db, """
            SELECT
                COALESCE(NULLIF(channel, ''), 'cli') as tenant_id,
                COUNT(*) as interactions,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes,
                ROUND(SUM(cost_usd), 4) as total_cost_usd,
                ROUND(AVG(latency_ms), 1) as avg_latency_ms,
                MIN(timestamp) as first_seen,
                MAX(timestamp) as last_seen
            FROM interaction_log
            GROUP BY tenant_id
            ORDER BY interactions DESC
        """)

        return web.json_response({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tenants": tenants,
            "total_tenants": len(tenants),
        })

    async def _handle_tenant_dashboard(self, request) -> "web.Response":
        """GET /tenant/{tenant_id}/dashboard — Per-tenant full dashboard."""
        tenant_id = request.match_info["tenant_id"]
        db = self._get_interaction_db()

        summary = self._safe_query_one(db, """
            SELECT
                COUNT(*) as total_interactions,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes,
                ROUND(SUM(cost_usd), 4) as total_cost_usd,
                ROUND(AVG(latency_ms), 1) as avg_latency_ms,
                MIN(timestamp) as first_seen,
                MAX(timestamp) as last_seen
            FROM interaction_log
            WHERE COALESCE(NULLIF(channel, ''), 'cli') = ?
        """, (tenant_id,))

        tier_breakdown = self._safe_query(db, """
            SELECT
                selected_tier,
                COUNT(*) as volume,
                ROUND(CAST(SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS REAL)
                      / MAX(COUNT(*), 1) * 100, 2) as success_rate_pct,
                ROUND(SUM(cost_usd), 4) as cost_usd
            FROM interaction_log
            WHERE COALESCE(NULLIF(channel, ''), 'cli') = ?
            GROUP BY selected_tier
            ORDER BY selected_tier
        """, (tenant_id,))

        recent = self._safe_query(db, """
            SELECT
                id, timestamp, selected_tier, selected_provider,
                domain, complexity_score, success, latency_ms, cost_usd
            FROM interaction_log
            WHERE COALESCE(NULLIF(channel, ''), 'cli') = ?
            ORDER BY timestamp DESC
            LIMIT 20
        """, (tenant_id,))

        return web.json_response({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tenant_id": tenant_id,
            "summary": summary or {},
            "tier_breakdown": tier_breakdown,
            "recent_interactions": recent,
        })

    # ── Server lifecycle ──────────────────────────────────────────────────────

    def build_app(self) -> "web.Application":
        app = web.Application()
        app.router.add_post("/webhook/github", self._handle_github)
        app.router.add_post("/webhook/stripe", self._handle_stripe)
        app.router.add_post("/webhook/telegram", self._handle_telegram)
        app.router.add_post("/webhook/custom", self._handle_custom)
        app.router.add_post("/webhook/custom/{event_type}", self._handle_custom)
        app.router.add_get("/health", self._handle_health)
        app.router.add_get("/status", self._handle_status)
        # Metrics endpoints
        app.router.add_get("/metrics", self._handle_metrics)
        app.router.add_get("/metrics/routing", self._handle_metrics_routing)
        app.router.add_get("/metrics/evolution", self._handle_metrics_evolution)
        app.router.add_get("/metrics/budget", self._handle_metrics_budget)
        app.router.add_get("/metrics/skills", self._handle_metrics_skills)
        app.router.add_get("/metrics/corpus", self._handle_metrics_corpus)
        app.router.add_get("/metrics/tenants", self._handle_metrics_tenants)
        app.router.add_get("/tenant/{tenant_id}/dashboard", self._handle_tenant_dashboard)
        return app

    async def start(self):
        if not AIOHTTP_AVAILABLE:
            raise RuntimeError("aiohttp required: pip install aiohttp")

        app = self.build_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        logger.info(f"Webhook server listening on {self.host}:{self.port}")
        logger.info(f"Endpoints:")
        logger.info(f"  POST http://{self.host}:{self.port}/webhook/github")
        logger.info(f"  POST http://{self.host}:{self.port}/webhook/stripe")
        logger.info(f"  POST http://{self.host}:{self.port}/webhook/telegram")
        logger.info(f"  POST http://{self.host}:{self.port}/webhook/custom")

        # Keep running
        try:
            await asyncio.Future()
        finally:
            await runner.cleanup()


async def start_server(port: int = 8080, host: str = "0.0.0.0") -> WebhookServer:
    """Start the webhook server and return the server instance"""
    server = WebhookServer(port=port, host=host)
    await server.start()
    return server


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="ATLAS Webhook Server")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on (default: 8080)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
    args = parser.parse_args()

    server = WebhookServer(port=args.port, host=args.host)

    # Example: log all events
    async def log_event(event: WebhookEvent):
        logger.info(f"Event: {event.source}/{event.event_type} (verified={event.verified})")

    server.on("github", log_event)
    server.on("stripe", log_event)

    asyncio.run(server.start())
