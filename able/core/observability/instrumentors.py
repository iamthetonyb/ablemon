"""
ABLE Tracing — Span-based instrumentation with JSONL + OpenInference/Phoenix backend.

Provides:
  - Span / ABLETracer — lightweight tracing with context propagation
  - JSONLExporter — append-only JSONL file exporter (no Phoenix needed)
  - OTelSpanExporter — emits spans as OpenTelemetry spans with OpenInference
    semantic conventions so Phoenix shows rich ABLE routing context
  - DualExporter — JSONL (always) + OTel (when Phoenix is configured)
  - create_tracer() — factory that auto-selects the right exporter
  - trace_provider_call / trace_function — decorators for auto-tracing

OpenInference attribute mapping (ABLE → standard):
  model           → llm.model_name
  input_text      → input.value
  output_text     → output.value
  input_tokens    → llm.token_count.prompt
  output_tokens   → llm.token_count.completion
  tier            → able.tier        (custom — visible in Phoenix as filter)
  complexity_score→ able.complexity_score
  domain          → able.domain

Span kinds (ABLE → OpenInference):
  llm       → LLM
  tool      → TOOL
  routing   → CHAIN
  skill     → AGENT
  memory    → RETRIEVER
  embedding → EMBEDDING
"""

from __future__ import annotations

import functools
import json
import logging
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Span:
    """Single trace span."""

    trace_id: str
    span_id: str
    name: str
    kind: str  # "llm" | "tool" | "routing" | "skill"
    attributes: Dict[str, Any]
    start_time: float
    end_time: Optional[float] = None
    status: str = "ok"  # "ok" | "error"
    parent_span_id: Optional[str] = None
    events: List[Dict[str, Any]] = field(default_factory=list)


class JSONLExporter:
    """Append-only JSONL trace exporter (fallback when Phoenix unavailable)."""

    def __init__(self, path: str = "data/traces.jsonl"):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    def export(self, span: Span) -> None:
        """Append a finished span to the JSONL file."""
        record = asdict(span)
        try:
            with open(self.path, "a") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
        except OSError as exc:
            logger.error("Failed to write span to %s: %s", self.path, exc)


class OTelSpanExporter:
    """
    Export finished ABLE spans as OpenTelemetry spans with OpenInference
    semantic conventions so they appear in Phoenix with full routing context.

    Gracefully no-ops when opentelemetry-sdk is not installed or when no
    tracer provider has been registered (i.e. Phoenix hasn't been set up).

    Attribute mapping (ABLE attr key → OpenInference convention key):
        model           → llm.model_name
        input_text      → input.value       (shown in Phoenix trace detail)
        prompt          → input.value
        output_text     → output.value
        response        → output.value
        input_tokens    → llm.token_count.prompt
        output_tokens   → llm.token_count.completion
        tier/domain/... → able.*            (custom, filterable in Phoenix)
    """

    _KIND_MAP: Dict[str, str] = {
        "llm": "LLM",
        "tool": "TOOL",
        "routing": "CHAIN",
        "skill": "AGENT",
        "memory": "RETRIEVER",
        "embedding": "EMBEDDING",
    }

    # ABLE attribute key → OpenInference semantic convention attribute key
    _ATTR_MAP: Dict[str, str] = {
        "model": "llm.model_name",
        "input_text": "input.value",
        "prompt": "input.value",
        "output_text": "output.value",
        "response": "output.value",
        "input_tokens": "llm.token_count.prompt",
        "output_tokens": "llm.token_count.completion",
    }

    # ABLE-specific attributes forwarded with "able." prefix
    _ABLE_ATTRS = (
        "tier",
        "complexity_score",
        "domain",
        "provider",
        "tenant_id",
        "latency_ms",
        "fallback_used",
        "routing_decision",
        "scorer_version",
        "budget_gated",
    )

    def __init__(self) -> None:
        self._available = False
        self._tracer: Any = None
        try:
            from opentelemetry import trace as _otel  # type: ignore[import-untyped]

            # get_tracer() returns a no-op tracer when no provider is registered.
            # We check for the real provider by inspecting the global state.
            _provider = _otel.get_tracer_provider()
            _provider_type = type(_provider).__name__
            if _provider_type not in ("ProxyTracerProvider", "NoOpTracerProvider"):
                # A real provider (e.g. Phoenix OTLP exporter) is active.
                self._tracer = _otel.get_tracer("able")
                self._available = True
                logger.debug("OTelSpanExporter: real tracer provider detected (%s)", _provider_type)
            else:
                # No Phoenix / real provider yet — will stay no-op.
                self._tracer = _otel.get_tracer("able")  # keep for future reuse
                logger.debug(
                    "OTelSpanExporter: no real tracer provider (%s) — spans won't reach Phoenix",
                    _provider_type,
                )
        except ImportError:
            logger.debug("OTelSpanExporter: opentelemetry-sdk not installed")

    @property
    def is_available(self) -> bool:
        return self._available

    def export(self, span: "Span") -> None:  # noqa: F821
        """Emit a finished ABLE span as an OTel span to the active tracer provider."""
        if self._tracer is None:
            return
        try:
            from opentelemetry import trace as _otel  # type: ignore[import-untyped]
            from opentelemetry.trace import SpanKind, StatusCode  # type: ignore[import-untyped]

            start_ns = int(span.start_time * 1_000_000_000)
            end_ns = int((span.end_time or time.time()) * 1_000_000_000)

            oi_kind = self._KIND_MAP.get(span.kind, "CHAIN")

            otel_span = self._tracer.start_span(
                span.name,
                kind=SpanKind.INTERNAL,
                start_time=start_ns,
            )

            # ── OpenInference semantic convention attributes ─────────────
            otel_span.set_attribute("openinference.span.kind", oi_kind)

            for able_key, oi_key in self._ATTR_MAP.items():
                val = span.attributes.get(able_key)
                if val is not None:
                    otel_span.set_attribute(
                        oi_key,
                        val if isinstance(val, (bool, int, float, str)) else str(val),
                    )

            # Computed token total
            pt = span.attributes.get("input_tokens") or 0
            ct = span.attributes.get("output_tokens") or 0
            if pt or ct:
                otel_span.set_attribute("llm.token_count.total", int(pt) + int(ct))

            # ── ABLE-specific attributes (filterable in Phoenix) ────────
            for key in self._ABLE_ATTRS:
                val = span.attributes.get(key)
                if val is not None:
                    otel_span.set_attribute(
                        f"able.{key}",
                        val if isinstance(val, (bool, int, float, str)) else str(val),
                    )

            # ── Status & errors ──────────────────────────────────────────
            if span.status == "error":
                otel_span.set_status(StatusCode.ERROR)
                for event in span.events:
                    if "error" in event:
                        otel_span.record_exception(Exception(event["error"]))
            else:
                otel_span.set_status(StatusCode.OK)

            otel_span.end(end_time=end_ns)

        except Exception as exc:
            # Never crash the gateway over tracing
            logger.debug("OTelSpanExporter.export failed: %s", exc)


class DualExporter:
    """
    Write every span to JSONL (always available, no deps) AND to OTel/Phoenix
    (when opentelemetry-sdk is installed and a real provider is registered).

    JSONL is the cold-storage fallback — it survives Phoenix restarts and
    provides a local audit trail independent of the observability stack.
    OTel gives the live Phoenix dashboard with rich filtering.
    """

    def __init__(self, jsonl_path: str = "data/traces.jsonl") -> None:
        self._jsonl = JSONLExporter(jsonl_path)
        self._otel = OTelSpanExporter()
        if self._otel.is_available:
            logger.info("DualExporter: JSONL + Phoenix OTel active")
        else:
            logger.debug("DualExporter: JSONL only (Phoenix OTel not ready)")

    @property
    def otel_active(self) -> bool:
        return self._otel.is_available

    def export(self, span: "Span") -> None:  # noqa: F821
        self._jsonl.export(span)
        self._otel.export(span)


def create_tracer(
    service_name: str = "able",
    traces_path: str = "data/traces.jsonl",
) -> "ABLETracer":
    """
    Factory: returns an ABLETracer backed by DualExporter (JSONL + OTel).

    When Phoenix is running (docker compose --profile observability up) the
    OTel backend is active and all spans flow to the Phoenix dashboard at
    http://localhost:6006 with full ABLE routing context.

    When Phoenix is not running, spans are written to the JSONL fallback only.
    No config change needed — the exporter auto-detects the active provider.
    """
    exporter = DualExporter(jsonl_path=traces_path)
    return ABLETracer(exporter=exporter, service_name=service_name)


class ABLETracer:
    """Span-based tracing with context propagation."""

    def __init__(
        self,
        exporter: Optional[Any] = None,
        service_name: str = "able",
    ):
        # Default to DualExporter so spans go to both JSONL and Phoenix
        # without any extra config.
        self._exporter = exporter if exporter is not None else DualExporter()
        self._service_name = service_name
        self._active_spans: Dict[str, Span] = {}

    def start_span(
        self,
        name: str,
        kind: str = "llm",
        attributes: Optional[Dict[str, Any]] = None,
        parent: Optional[Span] = None,
    ) -> Span:
        """Create and register a new span."""
        trace_id = parent.trace_id if parent else uuid.uuid4().hex
        span = Span(
            trace_id=trace_id,
            span_id=uuid.uuid4().hex,
            name=name,
            kind=kind,
            attributes=attributes or {},
            start_time=time.time(),
            parent_span_id=parent.span_id if parent else None,
        )
        self._active_spans[span.span_id] = span
        return span

    def end_span(self, span: Span, status: str = "ok") -> None:
        """End a span and export it."""
        span.end_time = time.time()
        span.status = status
        self._active_spans.pop(span.span_id, None)
        self._exporter.export(span)

    @contextmanager
    def trace(self, name: str, kind: str = "llm", **attributes: Any):
        """Context manager for tracing a block of code."""
        span = self.start_span(name, kind, attributes)
        try:
            yield span
        except Exception as exc:
            span.events.append({"error": str(exc), "type": type(exc).__name__})
            self.end_span(span, status="error")
            raise
        else:
            self.end_span(span)


# ── Decorators ───────────────────────────────────────────────────


def trace_provider_call(tracer: ABLETracer):
    """
    Decorator factory for instrumenting provider completion calls.

    Captures: model, tier, tokens, latency, complexity_score, domain,
    tenant_id, input_text, output_text (when present in kwargs or result).

    input_text → maps to OpenInference input.value  (shown in Phoenix trace detail)
    output_text → maps to OpenInference output.value
    """

    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            attrs = {
                k: kwargs[k]
                for k in (
                    "model",
                    "tier",
                    "complexity_score",
                    "domain",
                    "tenant_id",
                    "input_text",
                    "prompt",
                )
                if k in kwargs
            }
            span = tracer.start_span(
                name=f"provider.{func.__name__}", kind="llm", attributes=attrs
            )
            try:
                result = await func(*args, **kwargs)
                # Capture token counts if the result exposes them
                if hasattr(result, "usage"):
                    span.attributes["input_tokens"] = getattr(
                        result.usage, "input_tokens", 0
                    )
                    span.attributes["output_tokens"] = getattr(
                        result.usage, "output_tokens", 0
                    )
                if hasattr(result, "latency_ms"):
                    span.attributes["latency_ms"] = result.latency_ms
                # Capture output content for Phoenix trace detail view
                if hasattr(result, "content") and result.content:
                    span.attributes["output_text"] = result.content[:4000]
                tracer.end_span(span)
                return result
            except Exception as exc:
                span.events.append({"error": str(exc), "type": type(exc).__name__})
                tracer.end_span(span, status="error")
                raise

        return wrapper

    return decorator


def trace_function(tracer: ABLETracer, name: Optional[str] = None):
    """Generic decorator for tracing any async function."""

    def decorator(func: Callable):
        span_name = name or func.__qualname__

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            span = tracer.start_span(name=span_name, kind="tool")
            try:
                result = await func(*args, **kwargs)
                tracer.end_span(span)
                return result
            except Exception as exc:
                span.events.append({"error": str(exc), "type": type(exc).__name__})
                tracer.end_span(span, status="error")
                raise

        return wrapper

    return decorator
