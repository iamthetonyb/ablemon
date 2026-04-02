#!/usr/bin/env python3
"""
Tests for thinking token dual-path: preserve raw thinking for distillation
while still stripping from user-facing output.
"""

import os
import sys

# Ensure able package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from core.providers.base import CompletionResult, UsageStats, strip_thinking_tokens


def _make_result(content: str) -> CompletionResult:
    """Helper to create a CompletionResult with minimal boilerplate."""
    return CompletionResult(
        content=content,
        finish_reason="stop",
        usage=UsageStats(input_tokens=10, output_tokens=20, total_tokens=30),
        provider="test",
        model="test-model",
    )


# ═══════════════════════════════════════════════════════════════
# SINGLE THINK BLOCK
# ═══════════════════════════════════════════════════════════════

def test_single_think_block_preserves_and_strips():
    """Single <think> block: content stripped, thinking_content preserved."""
    result = _make_result("<think>some reasoning</think>Final answer")
    result.strip_thinking()

    assert result.content == "Final answer"
    assert result.thinking_content == "some reasoning"
    assert result.has_thinking is True


# ═══════════════════════════════════════════════════════════════
# MULTIPLE THINK BLOCKS
# ═══════════════════════════════════════════════════════════════

def test_multiple_think_blocks_joined():
    """Multiple <think> blocks: all preserved, joined with newline."""
    result = _make_result(
        "<think>step one</think>Middle text<think>step two</think>Final answer"
    )
    result.strip_thinking()

    assert result.content == "Middle textFinal answer"
    assert result.thinking_content == "step one\nstep two"
    assert result.has_thinking is True


# ═══════════════════════════════════════════════════════════════
# NO THINK BLOCKS
# ═══════════════════════════════════════════════════════════════

def test_no_think_blocks():
    """No thinking tokens: thinking_content stays None, has_thinking False."""
    result = _make_result("Just a normal answer")
    result.strip_thinking()

    assert result.content == "Just a normal answer"
    assert result.thinking_content is None
    assert result.has_thinking is False


# ═══════════════════════════════════════════════════════════════
# UNCLOSED THINK TAGS (Nemotron pattern)
# ═══════════════════════════════════════════════════════════════

def test_unclosed_think_tag_stripped():
    """Unclosed <think> or 'Thinking:' preamble: strip still works."""
    result = _make_result("Thinking: let me reason about this\n\nActual answer here")
    result.strip_thinking()

    # The unclosed pattern doesn't match re.findall for closed tags,
    # so thinking_content stays None — but content is still stripped.
    assert "Thinking:" not in result.content
    assert "Actual answer here" in result.content
    assert result.thinking_content is None
    assert result.has_thinking is False


def test_unclosed_think_xml_tag():
    """Unclosed <think> at the start: stripped from content."""
    result = _make_result("<think>some partial reasoning\n\nActual answer")
    result.strip_thinking()

    assert "Actual answer" in result.content
    # Unclosed tag won't match the closed-tag regex, so no preservation
    assert result.thinking_content is None


# ═══════════════════════════════════════════════════════════════
# FREE FUNCTION BACKWARD COMPATIBILITY
# ═══════════════════════════════════════════════════════════════

def test_strip_thinking_tokens_free_function_unchanged():
    """strip_thinking_tokens() free function still works as before (backward compat)."""
    # Closed blocks
    assert strip_thinking_tokens("<think>blah</think>answer") == "answer"

    # Multiple blocks
    assert strip_thinking_tokens("<think>a</think>mid<think>b</think>end") == "midend"

    # No blocks
    assert strip_thinking_tokens("plain text") == "plain text"

    # Nemotron preamble
    result = strip_thinking_tokens("Thinking: stuff\n\nAnswer")
    assert "Answer" in result
    assert "Thinking:" not in result

    # Empty / None-ish
    assert strip_thinking_tokens("") == ""


# ═══════════════════════════════════════════════════════════════
# has_thinking PROPERTY
# ═══════════════════════════════════════════════════════════════

def test_has_thinking_true_when_content_present():
    """has_thinking returns True when thinking_content is a non-empty string."""
    result = _make_result("irrelevant")
    result.thinking_content = "some reasoning"
    assert result.has_thinking is True


def test_has_thinking_false_when_none():
    """has_thinking returns False when thinking_content is None."""
    result = _make_result("irrelevant")
    assert result.thinking_content is None
    assert result.has_thinking is False


def test_has_thinking_false_when_empty_string():
    """has_thinking returns False when thinking_content is empty string."""
    result = _make_result("irrelevant")
    result.thinking_content = ""
    assert result.has_thinking is False


# ═══════════════════════════════════════════════════════════════
# EDGE CASES
# ═══════════════════════════════════════════════════════════════

def test_empty_think_block():
    """Empty <think></think> block: no content to preserve."""
    result = _make_result("<think></think>answer")
    result.strip_thinking()

    assert result.content == "answer"
    # Empty string match still joins, but the result is empty
    assert result.thinking_content == ""


def test_none_content():
    """CompletionResult with None content: strip_thinking is a no-op."""
    result = _make_result("")
    result.content = None
    result.strip_thinking()

    assert result.content is None
    assert result.thinking_content is None
    assert result.has_thinking is False


def test_multiline_thinking():
    """Think block with newlines inside: preserved faithfully."""
    thinking = "line 1\nline 2\nline 3"
    result = _make_result(f"<think>{thinking}</think>Final")
    result.strip_thinking()

    assert result.content == "Final"
    assert result.thinking_content == thinking


def test_strip_thinking_returns_self():
    """strip_thinking() returns self for chaining."""
    result = _make_result("<think>x</think>y")
    returned = result.strip_thinking()
    assert returned is result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
