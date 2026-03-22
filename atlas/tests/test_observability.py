"""
Tests for atlas.core.observability — tracing, evaluators, tenant evaluator.
"""

import json
import os

import pytest

from atlas.core.observability.instrumentors import ATLASTracer, JSONLExporter, Span
from atlas.core.observability.evaluators import (
    ABLEEvaluator,
    HallucinationEvaluator,
    QACorrectnessEvaluator,
    SkillAdherenceEvaluator,
    ToneEvaluator,
)
from atlas.core.observability.phoenix_setup import PhoenixObserver
from atlas.core.observability.tenant_evaluator import TenantEvaluator


# ── Span / Tracer tests ─────────────────────────────────────────


class TestSpanCreation:
    def test_start_span_sets_fields(self):
        tracer = ATLASTracer(exporter=JSONLExporter(path=os.devnull))
        span = tracer.start_span("test-span", kind="tool", attributes={"key": "val"})

        assert span.name == "test-span"
        assert span.kind == "tool"
        assert span.attributes == {"key": "val"}
        assert span.start_time > 0
        assert span.end_time is None
        assert span.status == "ok"
        assert span.parent_span_id is None

    def test_end_span_sets_end_time_and_status(self):
        tracer = ATLASTracer(exporter=JSONLExporter(path=os.devnull))
        span = tracer.start_span("s")
        tracer.end_span(span, status="error")

        assert span.end_time is not None
        assert span.end_time >= span.start_time
        assert span.status == "error"

    def test_span_removed_from_active_after_end(self):
        tracer = ATLASTracer(exporter=JSONLExporter(path=os.devnull))
        span = tracer.start_span("s")
        assert span.span_id in tracer._active_spans
        tracer.end_span(span)
        assert span.span_id not in tracer._active_spans


class TestParentChildSpans:
    def test_child_inherits_trace_id(self):
        tracer = ATLASTracer(exporter=JSONLExporter(path=os.devnull))
        parent = tracer.start_span("parent")
        child = tracer.start_span("child", parent=parent)

        assert child.trace_id == parent.trace_id
        assert child.parent_span_id == parent.span_id
        assert child.span_id != parent.span_id

    def test_root_span_has_no_parent(self):
        tracer = ATLASTracer(exporter=JSONLExporter(path=os.devnull))
        root = tracer.start_span("root")
        assert root.parent_span_id is None


# ── JSONLExporter tests ──────────────────────────────────────────


class TestJSONLExporter:
    def test_export_produces_valid_jsonl(self, tmp_path):
        path = str(tmp_path / "traces.jsonl")
        exporter = JSONLExporter(path=path)
        tracer = ATLASTracer(exporter=exporter)

        span = tracer.start_span("a", kind="llm", attributes={"model": "gpt-5.4"})
        tracer.end_span(span)

        with open(path) as fh:
            lines = fh.readlines()

        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["name"] == "a"
        assert record["kind"] == "llm"
        assert record["attributes"]["model"] == "gpt-5.4"
        assert record["status"] == "ok"
        assert record["end_time"] is not None

    def test_export_appends_multiple_spans(self, tmp_path):
        path = str(tmp_path / "traces.jsonl")
        exporter = JSONLExporter(path=path)
        tracer = ATLASTracer(exporter=exporter)

        for i in range(3):
            span = tracer.start_span(f"span-{i}")
            tracer.end_span(span)

        with open(path) as fh:
            lines = fh.readlines()
        assert len(lines) == 3

    def test_creates_parent_directories(self, tmp_path):
        nested = str(tmp_path / "a" / "b" / "traces.jsonl")
        exporter = JSONLExporter(path=nested)
        tracer = ATLASTracer(exporter=exporter)
        span = tracer.start_span("x")
        tracer.end_span(span)

        assert os.path.isfile(nested)


# ── Context manager trace tests ──────────────────────────────────


class TestTraceContextManager:
    def test_trace_happy_path(self, tmp_path):
        path = str(tmp_path / "traces.jsonl")
        tracer = ATLASTracer(exporter=JSONLExporter(path=path))

        with tracer.trace("my-op", kind="tool", model="test") as span:
            assert isinstance(span, Span)
            assert span.end_time is None  # still running

        # After context manager exits, span should be exported
        with open(path) as fh:
            record = json.loads(fh.readline())
        assert record["status"] == "ok"
        assert record["end_time"] is not None

    def test_trace_records_error(self, tmp_path):
        path = str(tmp_path / "traces.jsonl")
        tracer = ATLASTracer(exporter=JSONLExporter(path=path))

        with pytest.raises(ValueError, match="boom"):
            with tracer.trace("fail-op") as span:
                raise ValueError("boom")

        with open(path) as fh:
            record = json.loads(fh.readline())
        assert record["status"] == "error"
        assert any("boom" in str(e) for e in record["events"])


# ── Evaluator tests ──────────────────────────────────────────────


class TestHallucinationEvaluator:
    def setup_method(self):
        self.evaluator = HallucinationEvaluator()

    def test_clean_text_scores_high(self):
        score = self.evaluator.score(
            "What is Python?",
            "Python is a programming language created by Guido van Rossum.",
        )
        assert score >= 0.85

    def test_hallucination_marker_lowers_score(self):
        score = self.evaluator.score(
            "Tell me about X",
            "I can confirm that X was published in 2025 by MIT researchers.",
        )
        assert score < 0.85

    def test_multiple_markers_drop_further(self):
        text = (
            "I can confirm that the study by Harvard in 2025 found "
            "exactly 42.5 percent improvement. As of today, "
            "version v3.2.1 was released on March 1st."
        )
        score = self.evaluator.score("Tell me", text)
        assert score < 0.6

    def test_empty_output_scores_perfect(self):
        # No hallucination in empty text
        assert self.evaluator.score("hi", "") == 1.0


class TestQACorrectnessEvaluator:
    def setup_method(self):
        self.evaluator = QACorrectnessEvaluator()

    def test_empty_output_zero(self):
        assert self.evaluator.score("How does X work?", "") == 0.0

    def test_relevant_answer_scores_high(self):
        score = self.evaluator.score(
            "How does Python garbage collection work?",
            "Python uses reference counting and a cyclic garbage collector.",
        )
        assert score > 0.6

    def test_irrelevant_answer_scores_lower(self):
        score = self.evaluator.score(
            "How does Python garbage collection work?",
            "The weather in Tokyo is sunny with temperatures around 25 degrees.",
        )
        relevant_score = self.evaluator.score(
            "How does Python garbage collection work?",
            "Python uses reference counting and a cyclic garbage collector.",
        )
        assert score < relevant_score


class TestSkillAdherenceEvaluator:
    def setup_method(self):
        self.evaluator = SkillAdherenceEvaluator()

    def test_no_spec_returns_neutral(self):
        assert self.evaluator.score("in", "out", skill_spec=None) == 0.7

    def test_matching_spec_scores_high(self):
        spec = "Write concise direct email with clear subject line"
        output = "Subject: Project update\n\nHere is a concise and direct summary."
        score = self.evaluator.score("write email", output, skill_spec=spec)
        assert score > 0.5


class TestToneEvaluator:
    def setup_method(self):
        self.evaluator = ToneEvaluator()

    def test_sycophantic_output_penalised(self):
        score = self.evaluator.score(
            "How do I fix this?",
            "Great question! I'd be happy to help! That's a wonderful idea!",
        )
        assert score < 0.6

    def test_direct_output_scores_well(self):
        score = self.evaluator.score(
            "How do I fix this?",
            "Here's the fix. Don't use eval — instead use ast.literal_eval.",
        )
        assert score >= 0.7


# ── ABLEEvaluator integration ───────────────────────────────────


class TestABLEEvaluator:
    def setup_method(self):
        self.evaluator = ABLEEvaluator()

    def test_evaluate_returns_all_keys(self):
        scores = self.evaluator.evaluate("What is X?", "X is a thing.")
        assert set(scores.keys()) == {
            "hallucination",
            "correctness",
            "skill_adherence",
            "tone",
        }
        assert all(0.0 <= v <= 1.0 for v in scores.values())

    def test_score_for_training_eligible(self):
        result = self.evaluator.score_for_training(
            "Explain Python decorators",
            "Decorators wrap functions to modify their behaviour. "
            "Here's how they work in Python with practical examples.",
        )
        assert "eligible" in result
        assert "scores" in result
        assert "average" in result
        assert isinstance(result["eligible"], bool)

    def test_score_for_training_ineligible_for_garbage(self):
        result = self.evaluator.score_for_training(
            "Explain Python decorators",
            "",
        )
        assert result["eligible"] is False

    def test_high_quality_response_eligible(self):
        result = self.evaluator.score_for_training(
            "What are Python decorators?",
            "Decorators are functions that wrap other functions to extend "
            "their behaviour without modifying the original code. "
            "Use the @decorator syntax above the function definition.",
        )
        # A clean, relevant, direct response should pass
        assert result["average"] >= 0.7


# ── PhoenixObserver fallback ─────────────────────────────────────


class TestPhoenixObserverFallback:
    def test_falls_back_gracefully(self):
        """Phoenix is almost certainly not installed in CI."""
        observer = PhoenixObserver(
            fallback_path="data/test_traces.jsonl"
        )
        # Should not raise, should report unavailable
        # (unless Phoenix happens to be installed, in which case it's available)
        assert isinstance(observer.is_available, bool)
        assert observer.fallback_path == "data/test_traces.jsonl"

    def test_create_tenant_project_returns_none_without_phoenix(self):
        observer = PhoenixObserver()
        if not observer.is_available:
            result = observer.create_tenant_project("acme")
            assert result is None


# ── TenantEvaluator tests ───────────────────────────────────────


class TestTenantEvaluator:
    def setup_method(self):
        self.evaluator = TenantEvaluator()

    def test_basic_tenant_eval(self):
        result = self.evaluator.evaluate_for_tenant(
            tenant_id="acme",
            input_text="Explain caching",
            output_text="Caching stores data closer to the consumer to reduce latency.",
        )
        assert result["tenant_id"] == "acme"
        assert "scores" in result
        assert "average" in result
        assert isinstance(result["passed"], bool)

    def test_required_keywords(self):
        result = self.evaluator.evaluate_for_tenant(
            tenant_id="t1",
            input_text="Summarise",
            output_text="Redis is great for caching. Memcached also works.",
            tenant_config={"required_keywords": ["redis", "memcached", "varnish"]},
        )
        # 2 out of 3 present
        assert "required_keywords" in result["scores"]
        assert 0.6 <= result["scores"]["required_keywords"] <= 0.7

    def test_banned_keywords(self):
        result = self.evaluator.evaluate_for_tenant(
            tenant_id="t1",
            input_text="Write copy",
            output_text="This product is absolutely amazing and wonderful!",
            tenant_config={"banned_keywords": ["amazing", "wonderful"]},
        )
        assert "banned_keywords" in result["scores"]
        assert result["scores"]["banned_keywords"] < 1.0


class TestTenantDriftDetection:
    def setup_method(self):
        self.evaluator = TenantEvaluator()

    def test_stable_scores_no_alert(self):
        result = self.evaluator.detect_drift(
            tenant_id="t1",
            recent_scores=[0.82, 0.81, 0.83],
            baseline_scores=[0.80, 0.82, 0.81],
        )
        assert result["direction"] == "stable"
        assert result["alert"] is False

    def test_degrading_scores_trigger_alert(self):
        result = self.evaluator.detect_drift(
            tenant_id="t1",
            recent_scores=[0.50, 0.55, 0.52],
            baseline_scores=[0.80, 0.82, 0.81],
        )
        assert result["direction"] == "degrading"
        assert result["alert"] is True
        assert result["drift"] < 0

    def test_improving_scores(self):
        result = self.evaluator.detect_drift(
            tenant_id="t1",
            recent_scores=[0.95, 0.93, 0.94],
            baseline_scores=[0.70, 0.72, 0.71],
        )
        assert result["direction"] == "improving"
        assert result["drift"] > 0

    def test_empty_data_no_crash(self):
        result = self.evaluator.detect_drift(
            tenant_id="t1",
            recent_scores=[],
            baseline_scores=[0.8],
        )
        assert result["alert"] is False
        assert result["reason"] == "insufficient data"
