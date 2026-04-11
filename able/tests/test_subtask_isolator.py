"""Tests for SubtaskIsolator — isolated sub-task execution contexts (Wove pattern)."""

from __future__ import annotations

import pytest

from able.core.execution.subtask_isolator import (
    IsolatedContext,
    Message,
    SubtaskIsolator,
)


def _msgs(n: int) -> list[Message]:
    return [Message(role="user" if i % 2 == 0 else "assistant", content=f"msg {i}") for i in range(n)]


class TestSubtaskIsolator:
    def test_isolate_returns_isolated_context(self):
        isolator = SubtaskIsolator()
        parent = _msgs(5)
        ctx = isolator.isolate("Summarize files", parent)
        assert isinstance(ctx, IsolatedContext)
        assert ctx.task_description == "Summarize files"
        assert ctx.task_id

    def test_isolate_trims_messages(self):
        isolator = SubtaskIsolator()
        parent = _msgs(20)
        ctx = isolator.isolate("task", parent, max_context_messages=5)
        # trimmed: 1 summary + 5 tail = 6 max
        assert len(ctx.messages) <= 6

    def test_isolate_keeps_all_when_under_limit(self):
        isolator = SubtaskIsolator()
        parent = _msgs(3)
        ctx = isolator.isolate("task", parent, max_context_messages=10)
        assert len(ctx.messages) == 3

    def test_isolate_prepends_summary_when_trimmed(self):
        isolator = SubtaskIsolator()
        parent = _msgs(15)
        ctx = isolator.isolate("task", parent, max_context_messages=5)
        # First message should be the synthetic context summary
        assert ctx.messages[0].role == "system"
        assert "omitted" in ctx.messages[0].content.lower()

    def test_depth_tracking(self):
        isolator = SubtaskIsolator()
        parent = _msgs(3)
        ctx = isolator.isolate("root task", parent)
        assert ctx.depth == 1

    def test_spawn_child_increments_depth(self):
        isolator = SubtaskIsolator()
        parent = _msgs(3)
        root = isolator.isolate("root", parent)
        child = isolator.spawn_child("child task", root)
        assert child.depth == 2

    def test_max_depth_raises(self):
        isolator = SubtaskIsolator(max_depth=2)
        parent = _msgs(3)
        root = isolator.isolate("L1", parent)
        child = isolator.spawn_child("L2", root)
        with pytest.raises(RuntimeError, match="max"):
            isolator.spawn_child("L3", child)

    def test_merge_back_appends_summary(self):
        isolator = SubtaskIsolator()
        parent = _msgs(3)
        ctx = isolator.isolate("task", parent)
        ctx.result = "Found 5 files"
        ctx.success = True
        merged = isolator.merge_back(ctx, parent)
        assert len(merged) == len(parent) + 1
        last = merged[-1]
        assert last.role == "assistant"
        assert "Found 5 files" in last.content

    def test_merge_back_does_not_mutate_parent(self):
        isolator = SubtaskIsolator()
        parent = _msgs(3)
        original_len = len(parent)
        ctx = isolator.isolate("task", parent)
        isolator.merge_back(ctx, parent)
        assert len(parent) == original_len

    def test_active_count_tracks_open_contexts(self):
        isolator = SubtaskIsolator()
        parent = _msgs(3)
        assert isolator.active_count() == 0
        ctx = isolator.isolate("task", parent)
        assert isolator.active_count() == 1
        isolator.merge_back(ctx, parent)
        assert isolator.active_count() == 0

    def test_merge_back_includes_tools_used(self):
        isolator = SubtaskIsolator()
        parent = _msgs(2)
        ctx = isolator.isolate("task", parent)
        ctx.tools_used = ["read_file", "grep_files"]
        ctx.success = True
        merged = isolator.merge_back(ctx, parent)
        summary = merged[-1].content
        assert "read_file" in summary
        assert "grep_files" in summary

    def test_failed_subtask_reflected_in_summary(self):
        isolator = SubtaskIsolator()
        parent = _msgs(2)
        ctx = isolator.isolate("risky task", parent)
        ctx.success = False
        ctx.result = "Permission denied"
        merged = isolator.merge_back(ctx, parent)
        assert "failed" in merged[-1].content.lower()
