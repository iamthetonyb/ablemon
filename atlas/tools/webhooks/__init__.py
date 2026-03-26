"""
ATLAS Webhook Server.

Receives events from external services (GitHub, Telegram, custom).
"""

from .server import WebhookServer, WebhookEvent, start_server

__all__ = ["WebhookServer", "WebhookEvent", "start_server"]
