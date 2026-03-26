"""
ATLAS Webhook Server.

Receives events from external services and routes them to the appropriate handlers.

Endpoints:
    POST /webhook/github      → GitHub push, PR, issue events
    POST /webhook/telegram    → Telegram updates (alternative to polling)
    POST /webhook/stripe      → Stripe payment events (if STRIPE_ENABLED)
    POST /webhook/custom      → Generic webhook receiver
    POST /api/billing/checkout  → Create Stripe checkout session (if enabled)
    POST /api/billing/subscribe → Create Stripe subscription (if enabled)
    GET  /api/billing/status    → Client billing status (if enabled)
    GET  /metrics             → JSON summary of all metrics
    GET  /metrics/routing     → Per-tier routing breakdown
    GET  /metrics/evolution   → Evolution daemon history
    GET  /metrics/budget      → Spend vs budget caps
    GET  /metrics/skills      → Skill usage and effectiveness
    GET  /metrics/corpus      → Distillation corpus stats
    GET  /metrics/tenants     → All tenants overview
    GET  /tenant/{id}/dashboard → Per-tenant dashboard

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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

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
    source: str                          # "github", "telegram", "custom"
    event_type: str                      # e.g. "push", "pull_request", "payment.succeeded"
    payload: Dict[str, Any]             # Raw payload from the source
    headers: Dict[str, str]             # Request headers
    timestamp: datetime = field(default_factory=datetime.utcnow)
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


# ─────────────────────────────────────────────────────────────────────────────
# Webhook Server
# ─────────────────────────────────────────────────────────────────────────────

class WebhookServer:
    """
    Lightweight webhook receiver for ATLAS.

    Handles incoming webhooks from external services, verifies signatures,
    normalizes events, and routes to registered handlers.
    """

    def __init__(self, port: int = 8080, host: str = "0.0.0.0",
                 db_path: str = "data/interaction_log.db"):
        self.port = port
        self.host = host
        self.db_path = db_path
        self._service_token = os.environ.get("ATLAS_SERVICE_TOKEN", "")
        self._custom_webhook_secret = os.environ.get("ATLAS_WEBHOOK_SECRET", "")
        self.handlers: Dict[str, List[Callable]] = {
            "github": [],
            "telegram": [],
            "custom": [],
        }
        self.secrets = {
            "github": os.environ.get("GITHUB_WEBHOOK_SECRET", ""),
        }
        self.event_log: List[WebhookEvent] = []

    def _verify_bearer_token(self, request) -> bool:
        """Check Authorization: Bearer <token> header against ATLAS_SERVICE_TOKEN."""
        if not self._service_token:
            return True  # No token configured = open (dev mode)
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return hmac.compare_digest(auth[7:], self._service_token)
        return False

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
        payload_bytes = await request.read()
        try:
            payload = json.loads(payload_bytes) if payload_bytes else {}
        except Exception:
            payload = {}

        # Verify signature if ATLAS_WEBHOOK_SECRET is configured
        verified = False
        if self._custom_webhook_secret:
            sig = request.headers.get("X-Atlas-Signature", "")
            expected = "sha256=" + hmac.new(
                self._custom_webhook_secret.encode(),
                payload_bytes,
                hashlib.sha256,
            ).hexdigest()
            if not sig or not hmac.compare_digest(expected, sig):
                return web.json_response({"error": "invalid signature"}, status=403)
            verified = True

        event_type = request.match_info.get("event_type", "custom")
        event = WebhookEvent(
            source="custom",
            event_type=event_type,
            payload=payload,
            headers=dict(request.headers),
            verified=verified,
        )

        logger.info(f"Custom webhook: {event_type} (verified={verified})")
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

        Requires Bearer token when ATLAS_SERVICE_TOKEN is set.
        """
        if not self._verify_bearer_token(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        atlas_home = Path.home() / ".atlas"
        status = {
            "system": "ATLAS",
            "status": "operational",
            "timestamp": datetime.utcnow().isoformat() + "Z",
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

    # ── Metrics endpoints ────────────────────────────────────────────────────

    def _get_hours(self, request) -> int:
        """Extract ?hours=N query param, default 24."""
        try:
            return int(request.rel_url.query.get("hours", "24"))
        except (ValueError, TypeError):
            return 24

    def _since_iso(self, hours: int) -> str:
        """Generate ISO timestamp for N hours ago."""
        return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    def _db_query(self, sql: str, params: tuple = ()) -> List[dict]:
        """Run a read query against interaction_log.db, return list of row dicts."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(sql, params).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()
        except Exception as e:
            logger.debug(f"Metrics query failed: {e}")
            return []

    def _db_query_one(self, sql: str, params: tuple = ()) -> dict:
        """Run a read query, return single row dict or empty dict."""
        results = self._db_query(sql, params)
        return results[0] if results else {}

    async def _handle_metrics(self, request) -> "web.Response":
        """GET /metrics — JSON summary of all metrics."""
        hours = self._get_hours(request)
        since = self._since_iso(hours)

        totals = self._db_query_one(
            """SELECT COUNT(*) as total_interactions,
                      SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes,
                      SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failures,
                      ROUND(SUM(cost_usd), 4) as total_cost_usd,
                      ROUND(AVG(latency_ms), 1) as avg_latency_ms,
                      SUM(input_tokens) as total_input_tokens,
                      SUM(output_tokens) as total_output_tokens
               FROM interaction_log WHERE timestamp >= ?""",
            (since,),
        )

        total = totals.get("total_interactions", 0) or 0
        failures = totals.get("failures", 0) or 0
        success_rate = round((total - failures) / total * 100, 2) if total > 0 else 0.0

        # Quality score averages (from evaluator)
        quality = self._db_query_one(
            """SELECT ROUND(AVG(quality_score), 4) as avg_quality,
                      SUM(CASE WHEN corpus_eligible = 1 THEN 1 ELSE 0 END) as corpus_eligible_count
               FROM interaction_log WHERE timestamp >= ? AND quality_score IS NOT NULL""",
            (since,),
        )

        return web.json_response({
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
        })

    async def _handle_metrics_routing(self, request) -> "web.Response":
        """GET /metrics/routing — Per-tier breakdown."""
        hours = self._get_hours(request)
        since = self._since_iso(hours)

        tiers = self._db_query(
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
            (since,),
        )

        domains = self._db_query(
            """SELECT domain, COUNT(*) as count,
                      ROUND(AVG(complexity_score), 3) as avg_score,
                      SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes
               FROM interaction_log WHERE timestamp >= ?
               GROUP BY domain ORDER BY count DESC""",
            (since,),
        )

        return web.json_response({
            "period_hours": hours,
            "by_tier": tiers,
            "by_domain": domains,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def _handle_metrics_evolution(self, request) -> "web.Response":
        """GET /metrics/evolution — Evolution daemon history."""
        hours = self._get_hours(request)

        # Read versioned weight backups from data/evolution_cycles/
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

        current_weights = {}
        weights_path = Path("config/scorer_weights.yaml")
        if weights_path.exists():
            try:
                with open(weights_path) as f:
                    current_weights = yaml.safe_load(f) or {}
            except Exception:
                pass

        since = self._since_iso(hours)
        drift = self._db_query(
            """SELECT scorer_version,
                      COUNT(*) as interactions,
                      ROUND(AVG(complexity_score), 3) as avg_score,
                      ROUND(AVG(CASE WHEN success = 1 THEN 1.0 ELSE 0.0 END) * 100, 2)
                          as success_rate_pct
               FROM interaction_log WHERE timestamp >= ?
               GROUP BY scorer_version ORDER BY scorer_version""",
            (since,),
        )

        return web.json_response({
            "period_hours": hours,
            "current_version": current_weights.get("version"),
            "current_weights": current_weights.get("features", {}),
            "domain_adjustments": current_weights.get("domain_adjustments", {}),
            "evolution_cycles": cycles,
            "scorer_version_drift": drift,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def _handle_metrics_budget(self, request) -> "web.Response":
        """GET /metrics/budget — Spend vs budget caps per tier."""
        hours = self._get_hours(request)
        since = self._since_iso(hours)

        cost_by_tier = self._db_query(
            """SELECT selected_tier,
                      ROUND(SUM(cost_usd), 4) as spent_usd,
                      COUNT(*) as interactions,
                      SUM(input_tokens) as input_tokens,
                      SUM(output_tokens) as output_tokens
               FROM interaction_log WHERE timestamp >= ?
               GROUP BY selected_tier ORDER BY selected_tier""",
            (since,),
        )

        # Load budget caps from scorer weights
        caps = {}
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

        opus_24h = self._db_query_one(
            """SELECT ROUND(SUM(cost_usd), 4) as spent
               FROM interaction_log
               WHERE selected_tier = 4 AND timestamp >= ?""",
            (self._since_iso(24),),
        )
        opus_30d = self._db_query_one(
            """SELECT ROUND(SUM(cost_usd), 4) as spent
               FROM interaction_log
               WHERE selected_tier = 4 AND timestamp >= ?""",
            (self._since_iso(24 * 30),),
        )

        return web.json_response({
            "period_hours": hours,
            "by_tier": cost_by_tier,
            "budget_caps": caps,
            "opus_spend": {
                "last_24h_usd": opus_24h.get("spent") or 0,
                "last_30d_usd": opus_30d.get("spent") or 0,
                "daily_remaining_usd": round(
                    caps.get("opus_daily_usd", 15.0)
                    - (opus_24h.get("spent") or 0), 4
                ),
                "monthly_remaining_usd": round(
                    caps.get("opus_monthly_usd", 100.0)
                    - (opus_30d.get("spent") or 0), 4
                ),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def _handle_metrics_skills(self, request) -> "web.Response":
        """GET /metrics/skills — Skill usage and effectiveness."""
        hours = self._get_hours(request)
        since = self._since_iso(hours)

        # Skills are tracked via the domain field and features JSON
        # For now, aggregate by domain as a proxy for skill usage
        domain_stats = self._db_query(
            """SELECT domain,
                      COUNT(*) as invocations,
                      SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes,
                      ROUND(CAST(SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS REAL)
                            / MAX(COUNT(*), 1) * 100, 2) as success_rate_pct,
                      ROUND(AVG(latency_ms), 1) as avg_latency_ms,
                      ROUND(SUM(cost_usd), 4) as total_cost_usd
               FROM interaction_log WHERE timestamp >= ?
               GROUP BY domain ORDER BY invocations DESC""",
            (since,),
        )

        # Skill index metadata if available
        skill_index = {}
        skill_index_path = Path("atlas/skills/SKILL_INDEX.yaml")
        if skill_index_path.exists():
            try:
                with open(skill_index_path) as f:
                    skill_index = yaml.safe_load(f) or {}
            except Exception:
                pass

        return web.json_response({
            "period_hours": hours,
            "by_domain": domain_stats,
            "registered_skills": len(skill_index.get("skills", [])) if isinstance(skill_index.get("skills"), list) else 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def _handle_metrics_corpus(self, request) -> "web.Response":
        """GET /metrics/corpus — Distillation corpus stats."""
        # Scan data/ for distillation JSONL files
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

        return web.json_response({
            "total_pairs": total_pairs,
            "total_size_bytes": total_size_bytes,
            "target_pairs": 200,
            "progress_pct": round(min(total_pairs / 200 * 100, 100), 1),
            "files": corpus_files,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def _handle_metrics_tenants(self, request) -> "web.Response":
        """GET /metrics/tenants — All tenants overview."""
        hours = self._get_hours(request)
        since = self._since_iso(hours)

        # Group interactions by channel as tenant proxy
        tenants = self._db_query(
            """SELECT channel as tenant_id,
                      COUNT(*) as interactions,
                      SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes,
                      ROUND(SUM(cost_usd), 4) as total_cost_usd,
                      ROUND(AVG(latency_ms), 1) as avg_latency_ms
               FROM interaction_log WHERE timestamp >= ?
               GROUP BY channel ORDER BY interactions DESC""",
            (since,),
        )

        return web.json_response({
            "period_hours": hours,
            "tenants": tenants,
            "total_tenants": len(tenants),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def _handle_tenant_dashboard(self, request) -> "web.Response":
        """GET /tenant/{tenant_id}/dashboard — Per-tenant dashboard."""
        tenant_id = request.match_info.get("tenant_id", "")
        hours = self._get_hours(request)
        since = self._since_iso(hours)

        summary = self._db_query_one(
            """SELECT COUNT(*) as interactions,
                      SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes,
                      ROUND(CAST(SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS REAL)
                            / MAX(COUNT(*), 1) * 100, 2) as success_rate_pct,
                      ROUND(SUM(cost_usd), 4) as total_cost_usd,
                      ROUND(AVG(latency_ms), 1) as avg_latency_ms,
                      ROUND(AVG(complexity_score), 3) as avg_complexity
               FROM interaction_log WHERE channel = ? AND timestamp >= ?""",
            (tenant_id, since),
        )

        by_tier = self._db_query(
            """SELECT selected_tier, COUNT(*) as volume,
                      ROUND(SUM(cost_usd), 4) as cost_usd
               FROM interaction_log WHERE channel = ? AND timestamp >= ?
               GROUP BY selected_tier ORDER BY selected_tier""",
            (tenant_id, since),
        )

        recent = self._db_query(
            """SELECT id, timestamp, domain, complexity_score, selected_tier,
                      success, latency_ms, cost_usd
               FROM interaction_log WHERE channel = ? AND timestamp >= ?
               ORDER BY timestamp DESC LIMIT 20""",
            (tenant_id, since),
        )

        # Distillation stats for this tenant
        distillation = {}
        try:
            from atlas.core.distillation.store import DistillationStore
            store = DistillationStore()
            distillation = store.stats(tenant_id=tenant_id)
        except Exception:
            pass

        # 0wav-specific ML pipeline stats
        ml_pipeline = {}
        if tenant_id == "0wav":
            try:
                from atlas.core.distillation.harvesters.owav_ml_harvester import OwavPipelineStats
                ml_pipeline = OwavPipelineStats().get_stats()
            except Exception:
                pass

        return web.json_response({
            "tenant_id": tenant_id,
            "period_hours": hours,
            "summary": summary,
            "by_tier": by_tier,
            "recent_interactions": recent,
            "distillation": distillation,
            "ml_pipeline": ml_pipeline,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    # ── Server lifecycle ──────────────────────────────────────────────────────

    def build_app(self) -> "web.Application":
        app = web.Application()
        app.router.add_post("/webhook/github", self._handle_github)
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

        # ── Payment integrations ────────────────────────────────
        # Stripe (credit cards) — adds /webhook/stripe, /api/billing/*
        try:
            from billing.stripe_billing import setup_stripe
            self._stripe_gate = setup_stripe(app)
        except Exception as e:
            self._stripe_gate = None
            logger.debug(f"Stripe not configured: {e}")

        # x402 (crypto/USDC) — adds middleware on /api/chat, /api/completion
        try:
            from billing.x402 import setup_x402
            self._x402_gate = setup_x402(app)
        except Exception as e:
            self._x402_gate = None
            logger.debug(f"x402 not configured: {e}")

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

    asyncio.run(server.start())
