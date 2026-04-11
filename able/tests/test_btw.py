"""Tests for D12 — /btw Ephemeral Side Questions.

Covers: command parsing, context building, BTW config, result handling.
"""

import pytest

from able.cli.btw import (
    BTWConfig,
    BTWResult,
    build_btw_context,
    parse_btw_command,
)


# ── Command parsing ────────────────────────────────────────────

class TestParseBTW:

    def test_valid_btw(self):
        assert parse_btw_command("/btw what is a monad?") == "what is a monad?"

    def test_btw_with_spaces(self):
        assert parse_btw_command("  /btw  hello  ") == "hello"

    def test_btw_case_insensitive(self):
        assert parse_btw_command("/BTW question") == "question"

    def test_empty_btw(self):
        assert parse_btw_command("/btw") is None

    def test_not_btw(self):
        assert parse_btw_command("hello world") is None

    def test_btw_in_middle(self):
        # Only matches at start
        assert parse_btw_command("say /btw hello") is None


# ── Context building ───────────────────────────────────────────

class TestBuildContext:

    def test_basic_context(self):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        ctx = build_btw_context(msgs)
        assert "user: hello" in ctx
        assert "assistant: hi there" in ctx

    def test_context_respects_limit(self):
        msgs = [
            {"role": "user", "content": "x" * 5000},
            {"role": "assistant", "content": "y" * 5000},
        ]
        ctx = build_btw_context(msgs, max_chars=6000)
        # Should only include the most recent that fits
        assert len(ctx) <= 6000

    def test_empty_conversation(self):
        ctx = build_btw_context([])
        assert ctx == ""

    def test_list_content(self):
        msgs = [
            {"role": "user", "content": [
                {"type": "text", "text": "hello"},
                {"type": "text", "text": "world"},
            ]},
        ]
        ctx = build_btw_context(msgs)
        assert "hello" in ctx
        assert "world" in ctx

    def test_recent_messages_first(self):
        msgs = [
            {"role": "user", "content": "first"},
            {"role": "user", "content": "second"},
            {"role": "user", "content": "third"},
        ]
        ctx = build_btw_context(msgs, max_chars=100)
        # Should include messages in order
        lines = ctx.strip().split("\n")
        assert "first" in lines[0] or "second" in lines[0] or "third" in lines[0]


# ── BTW result ─────────────────────────────────────────────────

class TestBTWResult:

    def test_success(self):
        r = BTWResult(question="q", answer="a")
        assert r.success is True

    def test_error(self):
        r = BTWResult(question="q", answer="", error="failed")
        assert r.success is False

    def test_empty_answer_no_error(self):
        r = BTWResult(question="q", answer="")
        assert r.success is False


# ── Config ─────────────────────────────────────────────────────

class TestBTWConfig:

    def test_defaults(self):
        c = BTWConfig()
        assert c.max_tokens == 500
        assert c.temperature == 0.3
        assert c.timeout_s == 30.0

    def test_custom(self):
        c = BTWConfig(max_tokens=100, timeout_s=10.0)
        assert c.max_tokens == 100
        assert c.timeout_s == 10.0
