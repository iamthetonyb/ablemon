"""
ATLAS Tracing — Span-based instrumentation with JSONL fallback.

Provides:
  - Span / ATLASTracer — lightweight tracing with context propagation
  - JSONLExporter — append-only JSONL file exporter (no Phoenix needed)
  - trace_provider_call / trace_function — decorators for auto-tracing
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


class ATLASTracer:
    """Span-based tracing with context propagation."""

    def __init__(
        self,
        exporter: Optional[JSONLExporter] = None,
        service_name: str = "atlas",
    ):
        self._exporter = exporter or JSONLExporter()
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


def trace_provider_call(tracer: ATLASTracer):
    """
    Decorator factory for instrumenting provider completion calls.

    Captures: model, tier, tokens, latency, complexity_score, domain,
    tenant_id (when present in kwargs).
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
                tracer.end_span(span)
                return result
            except Exception as exc:
                span.events.append({"error": str(exc), "type": type(exc).__name__})
                tracer.end_span(span, status="error")
                raise

        return wrapper

    return decorator


def trace_function(tracer: ATLASTracer, name: Optional[str] = None):
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
