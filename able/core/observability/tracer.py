"""
ABLE Operation Tracer — Emit spans to Phoenix for ANY operation.

The PhoenixObserver auto-instruments OpenAI/Anthropic API calls, but
cron jobs, research, distillation, and other automated operations are
invisible. This module provides a simple API to trace those operations.

Usage:
    from able.core.observability.tracer import trace_operation, get_tracer

    # Context manager style
    with trace_operation("cron.research_scout", attributes={...}) as span:
        result = do_work()
        span.set_attribute("result_count", len(result))

    # Decorator style
    @traced("distillation.harvest")
    async def harvest():
        ...

    # Direct tracer access
    tracer = get_tracer("able.evolution")
    with tracer.start_as_current_span("analyze_cycle") as span:
        ...
"""

import functools
import logging
import os
import time
from contextlib import contextmanager
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_tracer_cache: Dict[str, Any] = {}
_initialized = False


def _ensure_initialized():
    """Initialize OTel tracer provider if not already done by PhoenixObserver."""
    global _initialized
    if _initialized:
        return True

    try:
        from opentelemetry import trace
        provider = trace.get_tracer_provider()
        # Check if a real provider is registered (not the default no-op)
        if hasattr(provider, "get_tracer"):
            _initialized = True
            return True

        # No provider registered — try to register one ourselves
        from arize_phoenix.otel import register
        endpoint = os.environ.get(
            "PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006/v1/traces"
        )
        register(project_name="able", endpoint=endpoint)
        _initialized = True
        return True
    except ImportError:
        logger.debug("OTel/Phoenix not installed — tracing disabled")
        return False
    except Exception as e:
        logger.debug("Phoenix tracer init failed: %s", e)
        return False


def get_tracer(name: str = "able") -> Any:
    """Get an OTel tracer by name. Returns a no-op tracer if Phoenix unavailable."""
    if name in _tracer_cache:
        return _tracer_cache[name]

    if not _ensure_initialized():
        # Return a minimal no-op tracer
        _tracer_cache[name] = _NoOpTracer()
        return _tracer_cache[name]

    from opentelemetry import trace
    tracer = trace.get_tracer(name)
    _tracer_cache[name] = tracer
    return tracer


@contextmanager
def trace_operation(
    operation_name: str,
    attributes: Optional[Dict[str, Any]] = None,
    tracer_name: str = "able",
):
    """
    Context manager that creates an OTel span for any ABLE operation.

    with trace_operation("cron.interaction_audit", {"batch_size": 10}):
        run_audit()
    """
    tracer = get_tracer(tracer_name)
    span = tracer.start_span(operation_name)

    try:
        if attributes:
            for k, v in attributes.items():
                _safe_set(span, k, v)

        span.set_attribute("able.component", tracer_name)
        span.set_attribute("able.operation", operation_name)

        yield span

        span.set_attribute("able.success", True)
    except Exception as e:
        span.set_attribute("able.success", False)
        span.set_attribute("able.error", str(e)[:500])
        if hasattr(span, "record_exception"):
            span.record_exception(e)
        raise
    finally:
        span.end()


def traced(operation_name: str, tracer_name: str = "able"):
    """
    Decorator that wraps a function in a Phoenix span.

    @traced("cron.billing_summary")
    async def billing_summary():
        ...
    """
    def decorator(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            with trace_operation(operation_name, tracer_name=tracer_name) as span:
                span.set_attribute("able.function", func.__name__)
                start = time.time()
                result = await func(*args, **kwargs)
                span.set_attribute("able.duration_s", time.time() - start)
                if isinstance(result, dict):
                    for k, v in list(result.items())[:10]:
                        _safe_set(span, f"able.result.{k}", v)
                return result

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            with trace_operation(operation_name, tracer_name=tracer_name) as span:
                span.set_attribute("able.function", func.__name__)
                start = time.time()
                result = func(*args, **kwargs)
                span.set_attribute("able.duration_s", time.time() - start)
                return result

        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def _safe_set(span, key: str, value: Any):
    """Set a span attribute, converting types as needed."""
    try:
        if value is None:
            return
        if isinstance(value, (str, int, float, bool)):
            span.set_attribute(key, value)
        elif isinstance(value, (list, tuple)):
            span.set_attribute(key, str(value)[:500])
        else:
            span.set_attribute(key, str(value)[:500])
    except Exception:
        pass


class _NoOpSpan:
    """Minimal no-op span for when Phoenix is unavailable."""
    def set_attribute(self, *a, **kw): pass
    def record_exception(self, *a, **kw): pass
    def end(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass


class _NoOpTracer:
    """Minimal no-op tracer."""
    def start_span(self, *a, **kw): return _NoOpSpan()
    def start_as_current_span(self, *a, **kw): return _NoOpSpan()
