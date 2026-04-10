"""Tests for able.core.gateway.t5_advisor — T5 cloud advisor escalation."""

import pytest

from able.core.gateway.t5_advisor import (
    ADVISOR_MAX_TOKENS,
    MAX_ADVISOR_CALLS,
    T5AdvisorState,
    _curate_context,
    maybe_escalate_to_advisor,
)
from able.core.providers.base import CompletionResult, UsageStats


def _make_result(content: str, model: str = "opus") -> CompletionResult:
    """Helper to build CompletionResult with required fields."""
    return CompletionResult(
        content=content,
        finish_reason="stop",
        usage=UsageStats(),
        provider="test",
        model=model,
    )


# ── T5AdvisorState ──────────────────────────────────────────────


def test_state_defaults():
    state = T5AdvisorState()
    assert state.advisor_calls_used == 0
    assert state.consecutive_failures == 0
    assert state.consecutive_empty_outputs == 0
    assert state.last_advisor_guidance == ""
    assert not state.budget_exhausted
    assert not state.is_stuck()


def test_state_budget_exhausted():
    state = T5AdvisorState(advisor_calls_used=MAX_ADVISOR_CALLS)
    assert state.budget_exhausted


def test_state_not_stuck_when_budget_exhausted():
    """Even with stuck signals, budget exhaustion prevents escalation."""
    state = T5AdvisorState(
        advisor_calls_used=MAX_ADVISOR_CALLS,
        consecutive_failures=10,
    )
    assert not state.is_stuck()


def test_state_stuck_on_failures():
    state = T5AdvisorState()
    state.record_tool_result(False)
    state.record_tool_result(False)
    assert not state.is_stuck()  # 2 failures, not yet stuck
    state.record_tool_result(False)
    assert state.is_stuck()  # 3 failures = stuck


def test_state_stuck_on_empty_outputs():
    state = T5AdvisorState()
    state.record_empty_output()
    assert not state.is_stuck()  # 1 empty, not yet stuck
    state.record_empty_output()
    assert state.is_stuck()  # 2 empty = stuck


def test_state_success_resets_failures():
    state = T5AdvisorState()
    state.record_tool_result(False)
    state.record_tool_result(False)
    state.record_tool_result(True)  # success resets
    assert state.consecutive_failures == 0
    assert not state.is_stuck()


def test_state_text_output_resets_all():
    state = T5AdvisorState()
    state.record_tool_result(False)
    state.record_tool_result(False)
    state.record_empty_output()
    state.record_text_output()
    assert state.consecutive_failures == 0
    assert state.consecutive_empty_outputs == 0


def test_state_failure_does_not_reset_empty():
    """Tool failure shouldn't reset the empty output counter."""
    state = T5AdvisorState()
    state.record_empty_output()
    state.record_tool_result(False)
    assert state.consecutive_empty_outputs == 1


# ── _curate_context ─────────────────────────────────────────────


def test_curate_context_basic():
    from able.core.providers.base import Message, Role
    msgs = [
        Message(role=Role.USER, content="Hello"),
        Message(role=Role.ASSISTANT, content="Hi there"),
    ]
    result = _curate_context("test task", msgs)
    assert "Task: test task" in result
    assert "[user] Hello" in result
    assert "[assistant] Hi there" in result


def test_curate_context_truncates_long_messages():
    from able.core.providers.base import Message, Role
    long_msg = "x" * 500
    msgs = [Message(role=Role.USER, content=long_msg)]
    result = _curate_context("task", msgs)
    assert "..." in result
    assert len(result) < 600  # truncated


def test_curate_context_limits_messages():
    from able.core.providers.base import Message, Role
    msgs = [Message(role=Role.USER, content=f"msg {i}") for i in range(10)]
    result = _curate_context("task", msgs, max_msgs=3)
    # Only last 3 messages
    assert "msg 7" in result
    assert "msg 8" in result
    assert "msg 9" in result
    assert "msg 0" not in result


def test_curate_context_truncates_task():
    from able.core.providers.base import Message, Role
    long_task = "t" * 1000
    result = _curate_context(long_task, [Message(role=Role.USER, content="hi")])
    assert len(result.split("\n\n")[0]) <= 510  # "Task: " + 500 chars


# ── maybe_escalate_to_advisor ───────────────────────────────────


@pytest.mark.asyncio
async def test_escalate_returns_none_when_not_stuck():
    state = T5AdvisorState()
    result = await maybe_escalate_to_advisor(state, "task", [], {}, None)
    assert result is None


@pytest.mark.asyncio
async def test_escalate_returns_none_when_no_providers():
    state = T5AdvisorState(consecutive_failures=3)
    result = await maybe_escalate_to_advisor(state, "task", [], {}, None)
    assert result is None


@pytest.mark.asyncio
async def test_escalate_calls_advisor_on_stuck():
    """When stuck and provider available, should return guidance."""
    from unittest.mock import AsyncMock, MagicMock

    from able.core.providers.base import Message, Role

    state = T5AdvisorState(consecutive_failures=3)
    msgs = [Message(role=Role.USER, content="help me")]

    mock_chain = MagicMock()
    mock_result = _make_result("Try using the search tool with a more specific query.")
    mock_chain.complete = AsyncMock(return_value=mock_result)
    mock_chain.providers = [MagicMock()]

    guidance = await maybe_escalate_to_advisor(
        state, "find the bug", msgs, {4: mock_chain}, mock_chain,
    )

    assert guidance == "Try using the search tool with a more specific query."
    assert state.advisor_calls_used == 1
    assert state.consecutive_failures == 0  # reset after escalation


@pytest.mark.asyncio
async def test_escalate_uses_t4_chain_first():
    """Should prefer T4 chain over T2."""
    from unittest.mock import AsyncMock, MagicMock

    from able.core.providers.base import Message, Role

    state = T5AdvisorState(consecutive_failures=3)
    msgs = [Message(role=Role.USER, content="stuck")]

    t4_chain = MagicMock()
    t4_result = _make_result("T4 advice")
    t4_chain.complete = AsyncMock(return_value=t4_result)
    t4_chain.providers = [MagicMock()]

    t2_chain = MagicMock()
    t2_chain.complete = AsyncMock()
    t2_chain.providers = [MagicMock()]

    guidance = await maybe_escalate_to_advisor(
        state, "task", msgs, {4: t4_chain, 2: t2_chain}, t2_chain,
    )

    assert guidance == "T4 advice"
    t4_chain.complete.assert_called_once()
    t2_chain.complete.assert_not_called()


@pytest.mark.asyncio
async def test_escalate_falls_back_to_t2():
    """If T4 not available, should use T2."""
    from unittest.mock import AsyncMock, MagicMock

    from able.core.providers.base import Message, Role

    state = T5AdvisorState(consecutive_failures=3)
    msgs = [Message(role=Role.USER, content="stuck")]

    t2_chain = MagicMock()
    t2_result = _make_result("T2 advice", model="gpt")
    t2_chain.complete = AsyncMock(return_value=t2_result)
    t2_chain.providers = [MagicMock()]

    guidance = await maybe_escalate_to_advisor(
        state, "task", msgs, {2: t2_chain}, t2_chain,
    )

    assert guidance == "T2 advice"


@pytest.mark.asyncio
async def test_escalate_handles_provider_failure():
    """If advisor call fails, should return None gracefully."""
    from unittest.mock import AsyncMock, MagicMock

    from able.core.providers.base import Message, Role

    state = T5AdvisorState(consecutive_failures=3)
    msgs = [Message(role=Role.USER, content="stuck")]

    mock_chain = MagicMock()
    mock_chain.complete = AsyncMock(side_effect=Exception("API timeout"))
    mock_chain.providers = [MagicMock()]

    guidance = await maybe_escalate_to_advisor(
        state, "task", msgs, {4: mock_chain}, mock_chain,
    )

    assert guidance is None
    assert state.advisor_calls_used == 0  # not counted on failure


@pytest.mark.asyncio
async def test_escalate_respects_budget():
    """After MAX_ADVISOR_CALLS, should not escalate even if stuck."""
    from unittest.mock import AsyncMock, MagicMock

    from able.core.providers.base import Message, Role

    state = T5AdvisorState(
        advisor_calls_used=MAX_ADVISOR_CALLS,
        consecutive_failures=5,
    )
    msgs = [Message(role=Role.USER, content="stuck")]

    mock_chain = MagicMock()
    mock_chain.complete = AsyncMock(
        return_value=_make_result("advice"),
    )
    mock_chain.providers = [MagicMock()]

    guidance = await maybe_escalate_to_advisor(
        state, "task", msgs, {4: mock_chain}, mock_chain,
    )

    assert guidance is None
    mock_chain.complete.assert_not_called()


@pytest.mark.asyncio
async def test_escalate_passes_correct_params():
    """Verify advisor call uses correct max_tokens and temperature."""
    from unittest.mock import AsyncMock, MagicMock

    from able.core.providers.base import Message, Role

    state = T5AdvisorState(consecutive_failures=3)
    msgs = [Message(role=Role.USER, content="test")]

    mock_chain = MagicMock()
    mock_result = _make_result("guidance")
    mock_chain.complete = AsyncMock(return_value=mock_result)
    mock_chain.providers = [MagicMock()]

    await maybe_escalate_to_advisor(
        state, "task", msgs, {4: mock_chain}, mock_chain,
    )

    call_kwargs = mock_chain.complete.call_args
    assert call_kwargs.kwargs["max_tokens"] == ADVISOR_MAX_TOKENS
    assert call_kwargs.kwargs["temperature"] == 0.3
    assert call_kwargs.kwargs["tools"] is None


@pytest.mark.asyncio
async def test_escalate_empty_guidance_not_counted():
    """Empty/whitespace guidance should not consume budget."""
    from unittest.mock import AsyncMock, MagicMock

    from able.core.providers.base import Message, Role

    state = T5AdvisorState(consecutive_failures=3)
    msgs = [Message(role=Role.USER, content="stuck")]

    mock_chain = MagicMock()
    mock_result = _make_result("  \n  ")
    mock_chain.complete = AsyncMock(return_value=mock_result)
    mock_chain.providers = [MagicMock()]

    guidance = await maybe_escalate_to_advisor(
        state, "task", msgs, {4: mock_chain}, mock_chain,
    )

    assert guidance is None
    assert state.advisor_calls_used == 0
