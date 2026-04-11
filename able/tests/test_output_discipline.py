"""Tests for OutputDiscipline guardrails (Wove pattern)."""

from __future__ import annotations

import pytest

from able.core.gateway.output_discipline import (
    DisciplineContext,
    DisciplineResult,
    OutputDiscipline,
)


class TestOutputDiscipline:
    def test_short_between_tools_passes(self):
        d = OutputDiscipline(between_tools_limit=50)
        ctx = DisciplineContext(pending_tool_calls=1)
        result = d.check_response("OK, fetching data now.", ctx)
        assert result.ok
        assert result.warning == ""

    def test_long_between_tools_fails(self):
        d = OutputDiscipline(between_tools_limit=5)
        ctx = DisciplineContext(pending_tool_calls=1)
        text = "This is a very long response that far exceeds the allowed word limit between tool calls in the pipeline."
        result = d.check_response(text, ctx)
        assert not result.ok
        assert len(result.warning) > 0
        assert result.word_count > 5

    def test_final_response_not_limited(self):
        d = OutputDiscipline(between_tools_limit=5)
        ctx = DisciplineContext(pending_tool_calls=0)  # no pending tools → final
        text = "This is a very long response " * 20
        result = d.check_response(text, ctx)
        assert result.ok
        assert result.limit == 0

    def test_is_between_tools_true_when_pending(self):
        d = OutputDiscipline()
        ctx = DisciplineContext(pending_tool_calls=2)
        assert d.is_between_tools(ctx)

    def test_is_between_tools_false_when_zero_pending(self):
        d = OutputDiscipline()
        ctx = DisciplineContext(pending_tool_calls=0)
        assert not d.is_between_tools(ctx)

    def test_violation_count_increments(self):
        d = OutputDiscipline(between_tools_limit=3)
        ctx = DisciplineContext(pending_tool_calls=1, session_id="sess1")
        long_text = "word " * 20
        d.check_response(long_text, ctx)
        d.check_response(long_text, ctx)
        assert d.violation_count("sess1") == 2

    def test_violation_count_per_session(self):
        d = OutputDiscipline(between_tools_limit=3)
        long = "word " * 20
        d.check_response(long, DisciplineContext(pending_tool_calls=1, session_id="A"))
        d.check_response(long, DisciplineContext(pending_tool_calls=1, session_id="B"))
        d.check_response(long, DisciplineContext(pending_tool_calls=1, session_id="B"))
        assert d.violation_count("A") == 1
        assert d.violation_count("B") == 2

    def test_reset_session_clears_count(self):
        d = OutputDiscipline(between_tools_limit=3)
        long = "word " * 20
        ctx = DisciplineContext(pending_tool_calls=1, session_id="s")
        d.check_response(long, ctx)
        assert d.violation_count("s") == 1
        d.reset_session("s")
        assert d.violation_count("s") == 0

    def test_trimmed_text_unchanged(self):
        d = OutputDiscipline(between_tools_limit=3)
        original = "one two three four five six seven"
        ctx = DisciplineContext(pending_tool_calls=1)
        result = d.check_response(original, ctx)
        assert result.trimmed_text == original

    def test_warning_escalates_after_3_violations(self):
        d = OutputDiscipline(between_tools_limit=3)
        long = "word " * 20
        ctx = DisciplineContext(pending_tool_calls=1, session_id="s")
        results = [d.check_response(long, ctx) for _ in range(4)]
        # 4th violation should mention escalation
        assert "auditor" in results[3].warning.lower() or "violation #4" in results[3].warning
