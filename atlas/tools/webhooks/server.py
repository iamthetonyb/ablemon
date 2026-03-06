"""
ATLAS Webhook Server.

Receives events from external services and routes them to the appropriate handlers.

Endpoints:
    POST /webhook/github    → GitHub push, PR, issue events
    POST /webhook/stripe    → Stripe payment events
    POST /webhook/telegram  → Telegram updates (alternative to polling)
    POST /webhook/custom    → Generic webhook receiver

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
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

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
        from pathlib import Path
        import yaml

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
