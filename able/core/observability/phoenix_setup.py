"""
Phoenix Observer — Optional Arize Phoenix integration for ABLE.

Local dashboard at http://localhost:6006.
Start with:  docker compose --profile observability up -d

The observer connects to the Phoenix server via OTLP HTTP.  The endpoint is
read from PHOENIX_COLLECTOR_ENDPOINT (default: http://localhost:6006/v1/traces).
When running inside the ABLE Docker container alongside the `phoenix` service
the env var is pre-set to http://phoenix:6006/v1/traces.

Falls back to JSONL tracing when Phoenix is unreachable or not installed.
"""

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Default endpoint — overridden by PHOENIX_COLLECTOR_ENDPOINT env var.
_DEFAULT_ENDPOINT = "http://localhost:6006/v1/traces"


class PhoenixObserver:
    """
    Wraps Arize Phoenix for ABLE.  Connects to an external Phoenix server
    (local container or remote) via OTLP HTTP.  Traces ALL model calls across
    all tiers and tenants.

    Falls back to JSONL tracing when Phoenix is not available.
    """

    def __init__(
        self,
        project_name: str = "able",
        endpoint: str | None = None,
        fallback_path: str = "data/traces.jsonl",
    ):
        # Honour env var first, then explicit arg, then default.
        self._endpoint = (
            os.environ.get("PHOENIX_COLLECTOR_ENDPOINT")
            or endpoint
            or _DEFAULT_ENDPOINT
        )
        self._phoenix_available = False
        self._fallback_path = fallback_path
        self._project_name = project_name
        self.session: Any = None
        self.tracer_provider: Any = None

        try:
            from phoenix.otel import register  # type: ignore[import-untyped]

            # Connect to external Phoenix server — do NOT call px.launch_app()
            # here; the server is managed by docker-compose (or the user).
            self.tracer_provider = register(
                project_name=project_name, endpoint=self._endpoint
            )
            self._phoenix_available = True
            logger.info("Phoenix observer connected → %s", self._endpoint)
        except ImportError:
            logger.info(
                "Phoenix not installed — using JSONL fallback at %s",
                fallback_path,
            )
        except Exception as exc:
            logger.warning(
                "Phoenix failed to connect (%s) — using JSONL fallback at %s",
                exc,
                fallback_path,
            )

    @property
    def is_available(self) -> bool:
        return self._phoenix_available

    @property
    def fallback_path(self) -> str:
        return self._fallback_path

    def create_tenant_project(self, tenant_id: str) -> Optional[Any]:
        """Create an isolated Phoenix project per tenant."""
        if not self._phoenix_available:
            return None
        try:
            from phoenix.otel import register  # type: ignore[import-untyped]

            return register(
                project_name=f"able-{tenant_id}",
                endpoint=self._endpoint,
            )
        except Exception as exc:
            logger.warning("Failed to create tenant project %s: %s", tenant_id, exc)
            return None
