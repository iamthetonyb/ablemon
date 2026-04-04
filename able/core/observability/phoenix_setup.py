"""
Phoenix Observer — Arize Phoenix integration for ABLE.

Local dashboard at http://localhost:6006.
Start with:  docker compose --profile observability up -d

HOW TRACES GET TO PHOENIX
─────────────────────────
After register() sets the global OTel tracer provider, this module calls
OpenInference auto-instrumentation on the OpenAI and Anthropic client
libraries.  Every API call those clients make (GPT-5.4-Mini T1, GPT-5.4 T2,
Claude Opus T4) automatically becomes an OTel span exported to Phoenix — no
changes to gateway code needed.

WHY PHOENIX WAS EMPTY BEFORE
─────────────────────────────
register() set the global OTel provider but nothing used the OTel API to emit
spans.  The gateway's ABLETracer writes to JSONL (separate system).
OpenInference instrumentors fix this by patching the actual HTTP client inside
openai/anthropic at import time.

SETUP (local)
─────────────
  pip install -r able/requirements-observability.txt
  docker compose --profile observability up -d
  # Run ABLE — traces appear at http://localhost:6006 immediately.

SETUP (Docker + observability profile)
────────────────────────────────────
  PROFILE=full docker compose --profile observability up -d
  # 'full' profile installs requirements-observability.txt in the container.

ENDPOINT
────────
Read from PHOENIX_COLLECTOR_ENDPOINT env var.
  Default (local):          http://localhost:6006/v1/traces
  Docker compose peer:      http://phoenix:6006/v1/traces  (set automatically)
"""

import logging
import os
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

# Default endpoint — overridden by PHOENIX_COLLECTOR_ENDPOINT env var.
# Phoenix's OTLP HTTP ingest path is /v1/traces.
_DEFAULT_ENDPOINT = "http://localhost:6006/v1/traces"


class PhoenixObserver:
    """
    Wraps Arize Phoenix for ABLE.

    1. Registers the global OTel tracer provider pointing at Phoenix.
    2. Auto-instruments OpenAI + Anthropic clients so every provider call
       emits an OTel span to Phoenix — no gateway code changes needed.

    Falls back gracefully (logs warning, no crash) when:
    - arize-phoenix-otel not installed
    - Phoenix server is unreachable
    - openinference packages not installed (partial instrumentation)
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
        self.tracer_provider: Any = None
        self._instrumented: List[str] = []

        try:
            from phoenix.otel import register  # type: ignore[import-untyped]

            # Register global OTel tracer provider → Phoenix server.
            # This makes opentelemetry.trace.get_tracer() return a Phoenix-backed tracer.
            self.tracer_provider = register(
                project_name=project_name,
                endpoint=self._endpoint,
            )
            self._phoenix_available = True
            logger.info("Phoenix observer connected → %s", self._endpoint)

            # Auto-instrument provider clients — this is what actually sends
            # traces to Phoenix.  Each instrumentor patches its library's HTTP
            # client at import time so every API call becomes an OTel span.
            self._instrumented = self._instrument_providers(self.tracer_provider)
            if self._instrumented:
                logger.info(
                    "Phoenix auto-instrumented providers: %s",
                    ", ".join(self._instrumented),
                )
            else:
                logger.warning(
                    "Phoenix connected but no providers instrumented. "
                    "Install requirements-observability.txt to get traces: "
                    "pip install arize-phoenix-otel openinference-instrumentation-openai "
                    "openinference-instrumentation-anthropic"
                )

        except ImportError:
            logger.info(
                "Phoenix not installed (arize-phoenix-otel missing) — "
                "JSONL fallback active at %s. "
                "Install: pip install -r able/requirements-observability.txt",
                fallback_path,
            )
        except Exception as exc:
            logger.warning(
                "Phoenix failed to connect (%s) — JSONL fallback at %s",
                exc,
                fallback_path,
            )

    # ── Provider auto-instrumentation ────────────────────────────────────────

    def _instrument_providers(self, tracer_provider: Any) -> List[str]:
        """
        Auto-instrument all LLM client libraries ABLE uses.
        Each instrumentor patches its library's internals so API calls emit
        OTel spans automatically — no gateway changes needed.
        Returns list of successfully instrumented provider names.
        """
        instrumented: List[str] = []

        # ── OpenAI (T1 GPT-5.4-Mini, T2 GPT-5.4 via OAuth endpoint) ─────────
        try:
            from openinference.instrumentation.openai import (  # type: ignore[import-untyped]
                OpenAIInstrumentor,
            )
            OpenAIInstrumentor().instrument(tracer_provider=tracer_provider)
            instrumented.append("openai")
        except ImportError:
            logger.debug("openinference-instrumentation-openai not installed — OpenAI not traced")
        except Exception as exc:
            logger.warning("OpenAI instrumentation failed: %s", exc)

        # ── Anthropic (T4 Claude Opus 4.6) ────────────────────────────────────
        try:
            from openinference.instrumentation.anthropic import (  # type: ignore[import-untyped]
                AnthropicInstrumentor,
            )
            AnthropicInstrumentor().instrument(tracer_provider=tracer_provider)
            instrumented.append("anthropic")
        except ImportError:
            logger.debug(
                "openinference-instrumentation-anthropic not installed — Anthropic not traced"
            )
        except Exception as exc:
            logger.warning("Anthropic instrumentation failed: %s", exc)

        # ── OpenRouter / generic OpenAI-compat (T2 fallbacks) ────────────────
        # OpenRouter uses openai.ChatCompletion with a custom base_url.
        # Already covered by OpenAIInstrumentor above — no separate instrumentor needed.

        return instrumented

    # ── Public API ───────────────────────────────────────────────────────────

    @property
    def is_available(self) -> bool:
        return self._phoenix_available

    @property
    def is_instrumented(self) -> bool:
        return bool(self._instrumented)

    @property
    def instrumented_providers(self) -> List[str]:
        return list(self._instrumented)

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

    def send_test_trace(self) -> bool:
        """
        Emit a single test span to verify the Phoenix pipeline works.
        Call this from the CLI to confirm traces are flowing before starting
        the full gateway:

            python -c "
            from able.core.observability.phoenix_setup import PhoenixObserver
            obs = PhoenixObserver()
            ok = obs.send_test_trace()
            print('Phoenix trace OK' if ok else 'Phoenix trace FAILED')
            "
        """
        if not self._phoenix_available or self.tracer_provider is None:
            logger.warning("send_test_trace: Phoenix not available")
            return False
        try:
            from opentelemetry import trace as _otel_trace

            tracer = _otel_trace.get_tracer("able.test")
            with tracer.start_as_current_span("able.test_span") as span:
                span.set_attribute("test", True)
                span.set_attribute("project", self._project_name)
                span.set_attribute("message", "ABLE Phoenix connectivity test")
            logger.info("Test trace sent to Phoenix at %s", self._endpoint)
            return True
        except Exception as exc:
            logger.warning("Test trace failed: %s", exc)
            return False
