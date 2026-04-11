"""
Sub-task Isolation (Wove pattern).

Manages isolated execution contexts for sub-tasks so that exploratory
or speculative work does not pollute the main conversation context.

Parent messages are trimmed to fit within max_context_messages before
spawning an isolated context. When the sub-task completes, only a compact
summary is merged back — the raw sub-task messages are discarded.

Plan item: Module 4 — Sub-task Isolation.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Hard cap on nesting depth — prevents recursive explosion
_MAX_DEPTH = 3


@dataclass
class Message:
    """Minimal message representation compatible with gateway message format."""

    role: str   # "user" | "assistant" | "system" | "tool"
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class IsolatedContext:
    """Snapshot of an isolated sub-task execution context."""

    task_id: str
    task_description: str
    messages: List[Message]   # Trimmed slice of parent context
    tools_used: List[str] = field(default_factory=list)
    result: Optional[str] = None
    success: bool = False
    depth: int = 0            # Nesting level (root sub-task = 1)
    summary: str = ""         # Compact result merged back to parent


class SubtaskIsolator:
    """Spawn and manage isolated execution contexts for sub-tasks.

    Usage::

        isolator = SubtaskIsolator()
        ctx = isolator.isolate("Summarize the repo structure", parent_messages)
        # ... execute sub-task, populate ctx.result / ctx.tools_used ...
        parent_messages = isolator.merge_back(ctx, parent_messages)
    """

    def __init__(self, max_depth: int = _MAX_DEPTH) -> None:
        self._max_depth = max_depth
        # active_depths[task_id] → depth
        self._active: dict[str, int] = {}

    # ── public API ───────────────────────────────────────────────────────────

    def isolate(
        self,
        task_description: str,
        parent_messages: List[Message],
        max_context_messages: int = 10,
        parent_task_id: Optional[str] = None,
        _depth: int = 1,
    ) -> IsolatedContext:
        """Create an IsolatedContext for *task_description*.

        Args:
            task_description: What the sub-task should accomplish.
            parent_messages: Full parent conversation history.
            max_context_messages: Max messages to carry into the isolated ctx.
            parent_task_id: ID of the spawning task (for depth tracking).
            _depth: Internal — nesting depth (callers should not set this).

        Returns:
            IsolatedContext ready for execution. Messages are trimmed/summarized
            from *parent_messages* to fit within *max_context_messages*.

        Raises:
            RuntimeError: If max nesting depth would be exceeded.
        """
        if _depth > self._max_depth:
            raise RuntimeError(
                f"Sub-task nesting depth {_depth} exceeds maximum {self._max_depth}. "
                "Cannot spawn further isolated contexts."
            )

        task_id = str(uuid.uuid4())
        trimmed = self._trim_messages(parent_messages, max_context_messages)

        ctx = IsolatedContext(
            task_id=task_id,
            task_description=task_description,
            messages=trimmed,
            depth=_depth,
        )
        self._active[task_id] = _depth
        logger.debug(
            "SubtaskIsolator: spawned task_id=%s depth=%d msgs=%d",
            task_id, _depth, len(trimmed),
        )
        return ctx

    def merge_back(
        self,
        isolated_ctx: IsolatedContext,
        parent_messages: List[Message],
    ) -> List[Message]:
        """Append a compact summary of *isolated_ctx* to *parent_messages*.

        The raw sub-task messages are NOT appended — only the summary.
        This prevents context pollution from exploratory sub-task work.

        Returns a new list (parent_messages is not mutated).
        """
        summary = self._build_summary(isolated_ctx)
        isolated_ctx.summary = summary

        new_msg = Message(
            role="assistant",
            content=summary,
            metadata={
                "source": "subtask",
                "task_id": isolated_ctx.task_id,
                "depth": isolated_ctx.depth,
            },
        )
        merged = list(parent_messages) + [new_msg]
        self._active.pop(isolated_ctx.task_id, None)
        logger.debug(
            "SubtaskIsolator: merged task_id=%s → parent now %d msgs",
            isolated_ctx.task_id, len(merged),
        )
        return merged

    def spawn_child(
        self,
        task_description: str,
        parent_ctx: IsolatedContext,
        max_context_messages: int = 10,
    ) -> IsolatedContext:
        """Spawn a child sub-task from an existing IsolatedContext.

        Depth is incremented automatically. Raises RuntimeError if limit hit.
        """
        return self.isolate(
            task_description=task_description,
            parent_messages=parent_ctx.messages,
            max_context_messages=max_context_messages,
            parent_task_id=parent_ctx.task_id,
            _depth=parent_ctx.depth + 1,
        )

    def active_count(self) -> int:
        """Number of currently active (unmerged) isolated contexts."""
        return len(self._active)

    # ── internals ────────────────────────────────────────────────────────────

    def _trim_messages(
        self, messages: List[Message], max_count: int
    ) -> List[Message]:
        """Return the most recent *max_count* messages.

        If the list is longer, prepend a synthetic system message summarizing
        the dropped portion so the sub-task has minimal orientation context.
        """
        if len(messages) <= max_count:
            return list(messages)

        dropped = len(messages) - max_count
        tail = messages[-max_count:]
        summary_msg = Message(
            role="system",
            content=(
                f"[Context summary] {dropped} earlier message(s) from the parent "
                "conversation have been omitted to fit the sub-task context window. "
                "Focus on the sub-task described in the next message."
            ),
            metadata={"auto_generated": True},
        )
        return [summary_msg] + list(tail)

    @staticmethod
    def _build_summary(ctx: IsolatedContext) -> str:
        status = "completed" if ctx.success else "failed"
        tools_part = (
            f" Tools used: {', '.join(ctx.tools_used)}." if ctx.tools_used else ""
        )
        result_part = f" Result: {ctx.result}" if ctx.result else " No result captured."
        return (
            f"[Sub-task {status}] Task: {ctx.task_description}.{tools_part}{result_part}"
        )
