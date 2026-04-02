"""
Trace Logger
Distributed tracing for agent operations and pipeline execution.
"""

import json
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime
from contextlib import contextmanager


@dataclass
class Span:
    """A single span in a trace"""
    span_id: str
    trace_id: str
    name: str
    start_time: datetime
    end_time: Optional[datetime] = None
    parent_span_id: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "ok"  # "ok", "error"
    error_message: Optional[str] = None


@dataclass
class TraceContext:
    """Context for trace propagation"""
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None


class TraceLogger:
    """
    Distributed tracing for ABLE operations.
    Tracks agent pipeline execution, tool calls, and security checks.
    """

    def __init__(self, traces_dir: Optional[Path] = None):
        self.traces_dir = traces_dir or Path(__file__).parent
        self.traces_dir.mkdir(parents=True, exist_ok=True)

        # Active spans
        self.active_spans: Dict[str, Span] = {}

        # Current trace context (thread-local in real impl)
        self._current_context: Optional[TraceContext] = None

    def _generate_id(self) -> str:
        """Generate unique ID"""
        return uuid.uuid4().hex[:16]

    def start_trace(self, name: str, attributes: Dict[str, Any] = None) -> TraceContext:
        """Start a new trace"""
        trace_id = self._generate_id()
        span_id = self._generate_id()

        span = Span(
            span_id=span_id,
            trace_id=trace_id,
            name=name,
            start_time=datetime.utcnow(),
            attributes=attributes or {}
        )

        self.active_spans[span_id] = span
        self._current_context = TraceContext(trace_id=trace_id, span_id=span_id)

        return self._current_context

    def start_span(
        self,
        name: str,
        parent_context: Optional[TraceContext] = None,
        attributes: Dict[str, Any] = None
    ) -> Span:
        """Start a new span"""
        context = parent_context or self._current_context

        if not context:
            # Start new trace if no context
            self.start_trace(name, attributes)
            context = self._current_context

        span_id = self._generate_id()
        span = Span(
            span_id=span_id,
            trace_id=context.trace_id,
            name=name,
            start_time=datetime.utcnow(),
            parent_span_id=context.span_id,
            attributes=attributes or {}
        )

        self.active_spans[span_id] = span
        return span

    def end_span(
        self,
        span: Span,
        status: str = "ok",
        error_message: Optional[str] = None
    ):
        """End a span"""
        span.end_time = datetime.utcnow()
        span.status = status
        span.error_message = error_message

        # Log span
        self._log_span(span)

        # Remove from active
        if span.span_id in self.active_spans:
            del self.active_spans[span.span_id]

    def add_event(
        self,
        span: Span,
        name: str,
        attributes: Dict[str, Any] = None
    ):
        """Add an event to a span"""
        event = {
            "name": name,
            "timestamp": datetime.utcnow().isoformat(),
            "attributes": attributes or {}
        }
        span.events.append(event)

    def set_attribute(self, span: Span, key: str, value: Any):
        """Set a span attribute"""
        span.attributes[key] = value

    def _log_span(self, span: Span):
        """Log span to file"""
        log_file = self.traces_dir / "traces.jsonl"

        duration_ms = None
        if span.end_time and span.start_time:
            duration_ms = (span.end_time - span.start_time).total_seconds() * 1000

        entry = {
            "trace_id": span.trace_id,
            "span_id": span.span_id,
            "parent_span_id": span.parent_span_id,
            "name": span.name,
            "start_time": span.start_time.isoformat(),
            "end_time": span.end_time.isoformat() if span.end_time else None,
            "duration_ms": duration_ms,
            "status": span.status,
            "error_message": span.error_message,
            "attributes": span.attributes,
            "events": span.events
        }

        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

    @contextmanager
    def trace(self, name: str, attributes: Dict[str, Any] = None):
        """Context manager for tracing"""
        span = self.start_span(name, attributes=attributes)
        try:
            yield span
            self.end_span(span, status="ok")
        except Exception as e:
            self.end_span(span, status="error", error_message=str(e))
            raise

    def trace_agent_pipeline(
        self,
        message: str,
        client_id: Optional[str] = None
    ) -> TraceContext:
        """Start tracing an agent pipeline execution"""
        return self.start_trace(
            name="agent_pipeline",
            attributes={
                "message_length": len(message),
                "client_id": client_id
            }
        )

    def trace_scanner(self, context: TraceContext, result: Dict[str, Any]) -> Span:
        """Trace scanner agent execution"""
        span = self.start_span(
            name="scanner_agent",
            parent_context=context,
            attributes={
                "threat_level": result.get("security_verdict", {}).get("threat_level"),
                "trust_score": result.get("security_verdict", {}).get("trust_score"),
                "passed": result.get("security_verdict", {}).get("passed")
            }
        )
        self.end_span(span)
        return span

    def trace_auditor(self, context: TraceContext, result: Dict[str, Any]) -> Span:
        """Trace auditor agent execution"""
        span = self.start_span(
            name="auditor_agent",
            parent_context=context,
            attributes={
                "overall_rating": result.get("overall_rating"),
                "approved": result.get("approved_for_executor")
            }
        )
        self.end_span(span)
        return span

    def trace_executor(self, context: TraceContext, action: str, result: Dict[str, Any]) -> Span:
        """Trace executor agent execution"""
        span = self.start_span(
            name="executor_agent",
            parent_context=context,
            attributes={
                "action": action,
                "executed": result.get("executed"),
                "required_approval": result.get("required_approval")
            }
        )
        self.end_span(span, status="ok" if result.get("executed") else "blocked")
        return span

    def get_trace(self, trace_id: str) -> List[Dict[str, Any]]:
        """Get all spans for a trace"""
        spans = []
        log_file = self.traces_dir / "traces.jsonl"

        if log_file.exists():
            with open(log_file) as f:
                for line in f:
                    entry = json.loads(line)
                    if entry.get("trace_id") == trace_id:
                        spans.append(entry)

        return sorted(spans, key=lambda s: s.get("start_time", ""))

    def get_recent_traces(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent traces"""
        traces = {}
        log_file = self.traces_dir / "traces.jsonl"

        if log_file.exists():
            with open(log_file) as f:
                for line in f:
                    entry = json.loads(line)
                    trace_id = entry.get("trace_id")
                    if trace_id:
                        if trace_id not in traces:
                            traces[trace_id] = {
                                "trace_id": trace_id,
                                "spans": [],
                                "start_time": entry.get("start_time")
                            }
                        traces[trace_id]["spans"].append(entry)

        # Sort by start time and return most recent
        sorted_traces = sorted(
            traces.values(),
            key=lambda t: t.get("start_time", ""),
            reverse=True
        )

        return sorted_traces[:limit]
