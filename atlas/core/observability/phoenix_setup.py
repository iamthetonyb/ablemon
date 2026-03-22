"""
Phoenix Observer — Optional Arize Phoenix integration for ATLAS.

Self-hosted at localhost:6006.  Traces all model calls across tiers and
tenants.  Falls back to JSONL tracing when Phoenix is not installed or
the server is unreachable.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class PhoenixObserver:
    """
    Wraps Arize Phoenix for ATLAS.  Self-hosted at localhost:6006.
    Traces ALL model calls across all tiers and tenants.

    Falls back to JSONL tracing when Phoenix is not available.
    """

    def __init__(
        self,
        project_name: str = "atlas",
        endpoint: str = "http://localhost:6006/v1/traces",
        fallback_path: str = "data/traces.jsonl",
    ):
        self._phoenix_available = False
        self._fallback_path = fallback_path
        self._project_name = project_name
        self._endpoint = endpoint
        self.session: Any = None
        self.tracer_provider: Any = None

        try:
            import phoenix as px  # type: ignore[import-untyped]
            from phoenix.otel import register  # type: ignore[import-untyped]

            self.session = px.launch_app(host="0.0.0.0", port=6006)
            self.tracer_provider = register(
                project_name=project_name, endpoint=endpoint
            )
            self._phoenix_available = True
            logger.info("Phoenix observer started at %s", endpoint)
        except ImportError:
            logger.info(
                "Phoenix not installed — using JSONL fallback at %s",
                fallback_path,
            )
        except Exception as exc:
            logger.warning(
                "Phoenix failed to start (%s) — using JSONL fallback at %s",
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
                project_name=f"atlas-{tenant_id}",
                endpoint=self._endpoint,
            )
        except Exception as exc:
            logger.warning("Failed to create tenant project %s: %s", tenant_id, exc)
            return None
