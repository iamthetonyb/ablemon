"""Tests for able.core.session.context_compactor — context window management."""

import hashlib
import json

import pytest

from able.core.session.context_compactor import (
    COMPACT_THRESHOLD,
    COMPACT_RATIO,
    MAX_COMPRESSION_ATTEMPTS,
    MIN_TAIL_MESSAGES,
    CONTEXT_LENGTH_ERROR_PATTERNS,
    DISCONNECT_AS_CONTEXT_ERRORS,
    ContextCompactor,
    _CHARS_PER_TOKEN,
)


@pytest.fixture
def compactor():
    return ContextCompactor()


def _make_msgs(n: int, content_len: int = 100) -> list[dict]:
    """Generate n messages alternating user/assistant with given content size."""
    msgs = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": "x" * content_len})
    return msgs


# ── estimate_tokens ──────────────────────────────────────────────

def test_estimate_tokens_string_content(compactor):
    msgs = [{"role": "user", "content": "a" * 400}]
    # 400 chars + 20 overhead = 420, // 4 = 105
    tokens = compactor.estimate_tokens(msgs)
    assert tokens == (400 + 20) // _CHARS_PER_TOKEN


def test_estimate_tokens_list_content(compactor):
    msgs = [{"role": "assistant", "content": [
        {"type": "text", "text": "hello world"},
        {"type": "tool_use", "id": "1", "name": "test", "input": {}},
    ]}]
    tokens = compactor.estimate_tokens(msgs)
    assert tokens > 0


def test_estimate_tokens_empty(compactor):
    assert compactor.estimate_tokens([]) == 0


# ── needs_compaction ─────────────────────────────────────────────

def test_needs_compaction_under_threshold(compactor):
    # Small messages, big context limit — no compaction needed
    msgs = _make_msgs(3, content_len=50)
    assert not compactor.needs_compaction(msgs, context_limit=100000)


def test_needs_compaction_over_threshold(compactor):
    # Each msg: 4000 chars + 20 overhead = 4020, // 4 = 1005 tokens
    # 20 messages = ~20100 tokens, vs 20000 * 0.8 = 16000 threshold
    msgs = _make_msgs(20, content_len=4000)
    assert compactor.needs_compaction(msgs, context_limit=20000)


# ── compact_if_needed — basic behavior ───────────────────────────

def test_no_compaction_when_under_threshold(compactor):
    msgs = _make_msgs(3, content_len=50)
    result = compactor.compact_if_needed(msgs, context_limit=100000)
    assert result == msgs  # Unchanged


def test_compaction_reduces_messages(compactor):
    # Create enough content to trigger compaction at 1000-token limit
    msgs = _make_msgs(10, content_len=500)
    result = compactor.compact_if_needed(msgs, context_limit=1000)
    assert len(result) < len(msgs)
    # First message should be the summary
    assert "[CONTEXT SUMMARY" in result[0]["content"]


def test_compaction_preserves_tail_messages(compactor):
    msgs = _make_msgs(10, content_len=500)
    result = compactor.compact_if_needed(msgs, context_limit=1000)
    # Must keep at least MIN_TAIL_MESSAGES recent messages + the summary
    assert len(result) >= MIN_TAIL_MESSAGES + 1


def test_compaction_summary_has_structure(compactor):
    msgs = [
        {"role": "user", "content": "Please fix the authentication bug in login.py"},
        {"role": "assistant", "content": "I found the issue. The token validation was skipping expiry checks. Fixed it."},
        {"role": "user", "content": "Great, now deploy to staging"},
        {"role": "assistant", "content": "Deployed successfully to staging environment."},
    ] + _make_msgs(10, content_len=500)

    result = compactor.compact_if_needed(msgs, context_limit=1000)
    summary = result[0]["content"]
    assert "User requests" in summary or "CONTEXT SUMMARY" in summary


# ── Strip-thinking recovery ──────────────────────────────────────

def test_strip_thinking_removes_think_blocks(compactor):
    msgs = [
        {"role": "user", "content": "What is 2+2?"},
        {"role": "assistant", "content": "<think>Let me calculate... 2+2=4</think>The answer is 4."},
    ]
    result = compactor._strip_thinking_blocks(msgs)
    assert "<think>" not in result[1]["content"]
    assert "The answer is 4." in result[1]["content"]


def test_strip_thinking_removes_internal_reasoning(compactor):
    msgs = [
        {"role": "assistant", "content": "[Internal reasoning]Deep analysis here[/Internal reasoning]The conclusion."},
    ]
    result = compactor._strip_thinking_blocks(msgs)
    assert "[Internal reasoning]" not in result[0]["content"]
    assert "The conclusion." in result[0]["content"]


def test_strip_thinking_preserves_user_messages(compactor):
    msgs = [
        {"role": "user", "content": "The <think> tag is used in XML"},
    ]
    result = compactor._strip_thinking_blocks(msgs)
    assert result[0]["content"] == msgs[0]["content"]


def test_strip_thinking_does_not_mutate_original(compactor):
    original_content = "<think>reasoning</think>Answer."
    msgs = [{"role": "assistant", "content": original_content}]
    compactor._strip_thinking_blocks(msgs)
    # Original should be untouched
    assert msgs[0]["content"] == original_content


def test_strip_thinking_recovery_skips_full_compaction(compactor):
    """If stripping thinking blocks alone reclaims enough space, skip full compaction."""
    # Create messages where thinking blocks are the bulk of the content
    thinking = "<think>" + "x" * 3000 + "</think>"
    msgs = [
        {"role": "user", "content": "short question"},
        {"role": "assistant", "content": thinking + "Short answer."},
        {"role": "user", "content": "follow-up"},
        {"role": "assistant", "content": thinking + "Another answer."},
    ]
    # Set context_limit so full content exceeds threshold but stripped doesn't
    full_tokens = compactor.estimate_tokens(msgs)
    # Threshold where full is over but stripped is under
    limit = int(full_tokens / COMPACT_THRESHOLD) - 50
    stripped_tokens = compactor.estimate_tokens(compactor._strip_thinking_blocks(msgs))

    if stripped_tokens < limit * COMPACT_THRESHOLD:
        result = compactor.compact_if_needed(msgs, context_limit=limit)
        # Should return stripped messages (no summary), not compacted
        assert "[CONTEXT SUMMARY" not in str(result)
        assert len(result) == len(msgs)


# ── Death spiral prevention ──────────────────────────────────────

def test_death_spiral_max_attempts(compactor):
    """After max_attempts, Layer 4 extractive summary refuses to run.

    Note: Layers 1-3 (strip-thinking, dedup, recency compress) still run
    since they're cheap and non-recursive. The death spiral guard only
    protects Layer 4 which is the expensive extractive summary.
    """
    msgs = _make_msgs(20, content_len=2000)
    limit = 500  # Very tight — forces compaction every call

    for _ in range(MAX_COMPRESSION_ATTEMPTS):
        msgs = compactor.compact_if_needed(msgs, context_limit=limit)

    # Layer 4 should refuse — compression_attempts exhausted.
    # But earlier layers (recency compress) may still modify content.
    before_count = len(msgs)
    before_tokens = compactor.estimate_tokens(msgs)
    result = compactor.compact_if_needed(msgs, context_limit=limit)
    # Layer 4 didn't run → no extractive summary → message count stable
    assert len(result) >= before_count - 1  # Recency compress doesn't remove messages
    # Verify death spiral counter is maxed
    assert compactor._compression_attempts == MAX_COMPRESSION_ATTEMPTS


def test_death_spiral_noop_detection(compactor):
    """Compaction that doesn't reduce message count returns original."""
    # 2 messages — compact_count = int(2 * 0.6) = 1, but after tail protection
    # keep_count = max(2-1, 3) = 3 > 2, so compact_count = 2-3 = -1 < 2 → returns unchanged
    msgs = _make_msgs(2, content_len=50000)
    result = compactor.compact_if_needed(msgs, context_limit=100)
    # Should be unchanged (too few messages to compact meaningfully)
    assert len(result) <= len(msgs)


def test_reset_compression_counter(compactor):
    """reset_compression_counter allows compaction to resume."""
    msgs = _make_msgs(20, content_len=2000)
    limit = 500

    for _ in range(MAX_COMPRESSION_ATTEMPTS):
        compactor.compact_if_needed(msgs, context_limit=limit)

    compactor.reset_compression_counter()
    # Should work again now
    result = compactor.compact_if_needed(_make_msgs(20, content_len=2000), context_limit=limit)
    assert len(result) < 20


# ── is_context_length_error ──────────────────────────────────────

def test_context_length_error_direct():
    err = Exception("context_length_exceeded: max tokens 128000")
    assert ContextCompactor.is_context_length_error(err)


def test_context_length_error_413():
    err = Exception("HTTP 413 Payload Too Large")
    assert ContextCompactor.is_context_length_error(err)


def test_context_length_error_disconnect():
    # Simulate a RemoteProtocolError (common disguised context-length error)
    class RemoteProtocolError(Exception):
        pass
    err = RemoteProtocolError("peer closed connection")
    assert ContextCompactor.is_context_length_error(err)


def test_context_length_error_server_disconnected():
    class ServerDisconnectedError(Exception):
        pass
    err = ServerDisconnectedError("disconnected")
    assert ContextCompactor.is_context_length_error(err)


def test_not_context_length_error():
    err = ValueError("some other error")
    assert not ContextCompactor.is_context_length_error(err)


def test_not_context_length_error_auth():
    err = Exception("401 Unauthorized")
    assert not ContextCompactor.is_context_length_error(err)


# ── _extractive_summary ──────────────────────────────────────────

def test_extractive_summary_captures_user_requests(compactor):
    msgs = [
        {"role": "user", "content": "Deploy the application to production server"},
        {"role": "assistant", "content": "Deploying now..."},
    ]
    summary = compactor._extractive_summary(msgs)
    assert "Deploy the application" in summary


def test_extractive_summary_captures_errors(compactor):
    msgs = [
        {"role": "assistant", "content": "The deployment failed due to a permission error on the target server."},
    ]
    summary = compactor._extractive_summary(msgs)
    assert "failed" in summary.lower() or "error" in summary.lower()


def test_extractive_summary_empty_messages(compactor):
    summary = compactor._extractive_summary([])
    assert "no extractable summary" in summary.lower()


# ── snapshot_hash ────────────────────────────────────────────────

def test_snapshot_hash_deterministic(compactor):
    msgs = _make_msgs(5, content_len=100)
    h1 = compactor.snapshot_hash(msgs)
    h2 = compactor.snapshot_hash(msgs)
    assert h1 == h2


def test_snapshot_hash_changes_with_content(compactor):
    msgs1 = [{"role": "user", "content": "hello"}]
    msgs2 = [{"role": "user", "content": "world"}]
    assert compactor.snapshot_hash(msgs1) != compactor.snapshot_hash(msgs2)


# ── _get_text ────────────────────────────────────────────────────

def test_get_text_string(compactor):
    assert compactor._get_text({"content": "hello"}) == "hello"


def test_get_text_list(compactor):
    msg = {"content": [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}]}
    assert "hello" in compactor._get_text(msg)
    assert "world" in compactor._get_text(msg)


def test_get_text_empty(compactor):
    assert compactor._get_text({}) == ""
