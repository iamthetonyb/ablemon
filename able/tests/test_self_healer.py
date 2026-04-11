"""Tests for D18 — Self-Healing Execution Loop.

Covers: empty output detection, error output detection, schema pattern
matching, blocked output detection, corrective context, retry budgeting, stats.
"""

import pytest

from able.core.gateway.self_healer import (
    HealingStats,
    SelfHealer,
    ValidationVerdict,
)


@pytest.fixture
def healer():
    return SelfHealer(max_retries_per_call=1)


# ── Empty output detection ──────────────────────────────────────

class TestEmptyOutput:

    def test_empty_web_search(self, healer):
        v = healer.validate("web_search", "", args={"query": "test"})
        assert not v.valid
        assert "Empty" in v.issue
        assert v.retry_recommended

    def test_empty_web_search_suggests_reformulation(self, healer):
        v = healer.validate("web_search", "", args={"query": "foo bar"})
        assert v.modified_args is not None
        assert "docs" in v.modified_args.get("query", "")

    def test_empty_read_file(self, healer):
        v = healer.validate("read_file", "  ", args={"path": "/tmp/x"})
        assert not v.valid
        assert v.retry_recommended

    def test_empty_shell(self, healer):
        v = healer.validate("shell", "", args={"command": "ls"})
        assert not v.valid

    def test_empty_ok_for_write_file(self, healer):
        v = healer.validate("write_file", "", args={"path": "/tmp/x"})
        assert v.valid  # write_file legitimately returns empty

    def test_short_output_ok(self, healer):
        v = healer.validate("web_search", "abc", args={"query": "test"})
        assert v.valid  # 3 chars = borderline, passes

    def test_whitespace_only_triggers(self, healer):
        v = healer.validate("memory_search", "   ", args={"query": "test"})
        assert not v.valid


# ── Error output detection ──────────────────────────────────────

class TestErrorOutput:

    def test_error_colon(self, healer):
        v = healer.validate("shell", "error: file not found")
        assert not v.valid
        assert "Error detected" in v.issue
        assert not v.retry_recommended  # Errors don't auto-retry

    def test_traceback(self, healer):
        v = healer.validate("shell", "Traceback (most recent call last):\n  File ...")
        assert not v.valid
        assert "traceback" in v.issue.lower()

    def test_command_not_found(self, healer):
        v = healer.validate("shell", "zsh: command not found: xyz")
        assert not v.valid

    def test_permission_denied(self, healer):
        v = healer.validate("shell", "Permission denied: /etc/shadow")
        assert not v.valid

    def test_error_only_checks_first_500(self, healer):
        # Error pattern beyond first 500 chars should not trigger
        output = "x" * 501 + "error: late error"
        v = healer.validate("shell", output)
        assert v.valid

    def test_normal_output_passes(self, healer):
        v = healer.validate("shell", "total 42\ndrwxr-xr-x 5 user staff")
        assert v.valid


# ── Schema pattern matching ─────────────────────────────────────

class TestSchemaPatterns:

    def test_with_no_schemas(self, healer):
        """No schemas = skip schema check."""
        v = healer.validate("read_file", "FileNotFoundError: x")
        # Should still catch via error output check
        assert not v.valid

    def test_with_mock_schema_registry(self):
        """Schema registry match_error returns an ErrorPattern."""
        class FakeEP:
            description = "File not found"
            error_type = "not_found"
            recovery = "Check the path"

        class FakeRegistry:
            def match_error(self, tool_name, output):
                if "NotFound" in output:
                    return FakeEP()
                return None

        healer = SelfHealer(tool_schemas=FakeRegistry())
        v = healer.validate("custom_tool", "NotFound: missing")
        assert not v.valid
        assert "not_found" in v.issue

    def test_schema_timeout_retryable(self):
        """Error type 'timeout' should be retry-recommended."""
        class FakeEP:
            description = "Timed out"
            error_type = "timeout"
            recovery = "Try again"

        class FakeRegistry:
            def match_error(self, tool_name, output):
                return FakeEP()

        healer = SelfHealer(tool_schemas=FakeRegistry())
        v = healer.validate("web_search", "timeout occurred", call_id="c1")
        assert not v.valid
        assert v.retry_recommended


# ── Blocked output detection ────────────────────────────────────

class TestBlockedOutput:

    def test_blocked_marker(self, healer):
        v = healer.validate("shell", "[BLOCKED] Repeated tool call detected")
        assert not v.valid
        assert "blocked by a guard" in v.issue
        assert not v.retry_recommended

    def test_blocked_embedded(self, healer):
        v = healer.validate("shell", "Some prefix [BLOCKED] rest")
        assert not v.valid

    def test_no_blocked(self, healer):
        v = healer.validate("shell", "BLOCK of text here")
        assert v.valid


# ── Corrective context ──────────────────────────────────────────

class TestCorrectiveContext:

    def test_basic_corrective(self, healer):
        verdict = ValidationVerdict(
            tool_name="web_search",
            valid=False,
            issue="Empty output",
            suggested_action="Reformulate query",
        )
        ctx = healer.corrective_context(verdict)
        assert "[SELF-HEAL]" in ctx
        assert "web_search" in ctx
        assert "Empty output" in ctx
        assert "Reformulate" in ctx

    def test_corrective_with_modified_args(self, healer):
        verdict = ValidationVerdict(
            tool_name="web_search",
            valid=False,
            issue="Empty",
            modified_args={"query": "site:docs test"},
        )
        ctx = healer.corrective_context(verdict)
        assert "modified args" in ctx
        assert "site:docs" in ctx

    def test_corrective_minimal(self, healer):
        verdict = ValidationVerdict(
            tool_name="shell",
            valid=False,
        )
        ctx = healer.corrective_context(verdict)
        assert "[SELF-HEAL]" in ctx
        assert "shell" in ctx


# ── Retry budgeting ─────────────────────────────────────────────

class TestRetryBudget:

    def test_first_retry_allowed(self, healer):
        v = healer.validate("web_search", "", args={"query": "x"}, call_id="c1")
        assert not v.valid
        assert v.retry_recommended

    def test_second_retry_exhausted(self, healer):
        # First failure
        healer.validate("web_search", "", args={"query": "x"}, call_id="c1")
        # Second attempt — budget exhausted, accepts output
        v = healer.validate("web_search", "", args={"query": "x"}, call_id="c1")
        assert v.valid  # Accepted despite empty
        assert "budget exhausted" in v.issue.lower()

    def test_different_call_ids_independent(self, healer):
        healer.validate("web_search", "", args={"query": "x"}, call_id="c1")
        v = healer.validate("web_search", "", args={"query": "y"}, call_id="c2")
        assert not v.valid  # Different call_id, fresh budget

    def test_no_call_id_no_tracking(self, healer):
        healer.validate("web_search", "", args={"query": "x"})
        v = healer.validate("web_search", "", args={"query": "x"})
        assert not v.valid  # No call_id = no retry tracking

    def test_reset_clears_retries(self, healer):
        healer.validate("web_search", "", args={"query": "x"}, call_id="c1")
        healer.reset()
        v = healer.validate("web_search", "", args={"query": "x"}, call_id="c1")
        assert not v.valid  # Fresh after reset


# ── Stats ───────────────────────────────────────────────────────

class TestStats:

    def test_initial_stats(self, healer):
        s = healer.stats()
        assert isinstance(s, HealingStats)
        assert s.validations == 0

    def test_stats_after_validations(self, healer):
        healer.validate("shell", "ls output here")
        healer.validate("web_search", "")
        s = healer.stats()
        assert s.validations == 2
        assert s.failures_detected == 1

    def test_stats_retries(self, healer):
        healer.validate("web_search", "", call_id="c1")
        s = healer.stats()
        assert s.retries_suggested == 1

    def test_record_successful_heal(self, healer):
        healer.record_successful_heal()
        s = healer.stats()
        assert s.successful_heals == 1
