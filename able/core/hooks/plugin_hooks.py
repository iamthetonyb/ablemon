"""
D10 — Plugin Lifecycle Hooks.

Fire-and-forget hook system for ABLE's execution pipeline.
Plugins register callbacks for lifecycle events; each callback
runs in isolation so a failing plugin never breaks the pipeline.

Forked from Hermes v0.5 PR #3542 + v0.8 enhancements.

Usage:
    hooks = HookRegistry()
    hooks.register("pre_tool_call", my_audit_hook)
    hooks.register("post_llm_call", my_logger_hook)

    # In the pipeline:
    await hooks.fire("pre_tool_call", tool_name="read_file", args={...})
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set, Union

logger = logging.getLogger(__name__)

VALID_HOOKS: Set[str] = {
    "pre_tool_call",
    "post_tool_call",
    "pre_llm_call",
    "post_llm_call",
    "on_session_start",
    "on_session_end",
    "on_session_finalize",
    "on_session_reset",
}

HookFn = Union[Callable[..., Any], Callable[..., Coroutine]]


@dataclass
class HookResult:
    """Result from a single hook execution."""
    hook_name: str
    plugin_name: str
    success: bool
    duration_ms: float = 0
    error: Optional[str] = None
    context_injection: Optional[Dict[str, Any]] = None


@dataclass
class HookFireResult:
    """Aggregate result from firing all hooks for an event."""
    event: str
    correlation_id: str
    results: List[HookResult] = field(default_factory=list)
    total_ms: float = 0

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.results if r.success)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.success)

    @property
    def context_injections(self) -> List[Dict[str, Any]]:
        return [r.context_injection for r in self.results if r.context_injection]


@dataclass
class _RegisteredHook:
    """Internal: a registered hook callback."""
    event: str
    fn: HookFn
    plugin_name: str
    priority: int = 0  # Lower = runs first


class HookRegistry:
    """Registry and executor for plugin lifecycle hooks.

    All hooks fire-and-forget: individual exceptions are caught and
    logged, never propagated. pre_llm_call hooks can return context
    injection dicts that get merged into the LLM call.
    """

    def __init__(self):
        self._hooks: Dict[str, List[_RegisteredHook]] = {e: [] for e in VALID_HOOKS}
        self._stats: Dict[str, int] = {"fires": 0, "successes": 0, "failures": 0}

    def register(
        self,
        event: str,
        fn: HookFn,
        plugin_name: str = "anonymous",
        priority: int = 0,
    ) -> None:
        """Register a hook callback for an event.

        Args:
            event: One of VALID_HOOKS.
            fn: Sync or async callable. Receives **kwargs from fire().
            plugin_name: Name for logging/diagnostics.
            priority: Lower values run first (default 0).

        Raises:
            ValueError: If event is not in VALID_HOOKS.
        """
        if event not in VALID_HOOKS:
            raise ValueError(
                f"Invalid hook event '{event}'. Valid: {sorted(VALID_HOOKS)}"
            )
        hook = _RegisteredHook(event=event, fn=fn, plugin_name=plugin_name, priority=priority)
        self._hooks[event].append(hook)
        self._hooks[event].sort(key=lambda h: h.priority)
        logger.debug("Registered hook: %s → %s (priority %d)", event, plugin_name, priority)

    def unregister(self, event: str, plugin_name: str) -> int:
        """Remove all hooks for a plugin on an event. Returns count removed."""
        if event not in self._hooks:
            return 0
        before = len(self._hooks[event])
        self._hooks[event] = [h for h in self._hooks[event] if h.plugin_name != plugin_name]
        return before - len(self._hooks[event])

    async def fire(
        self,
        event: str,
        correlation_id: Optional[str] = None,
        **kwargs,
    ) -> HookFireResult:
        """Fire all registered hooks for an event.

        Each hook runs in isolation — exceptions are caught and logged.
        For pre_llm_call, hooks can return dicts that are collected as
        context injections.

        Args:
            event: The lifecycle event.
            correlation_id: Request-scoped ID for tracing. Auto-generated if None.
            **kwargs: Passed to each hook callback.

        Returns:
            HookFireResult with per-hook results.
        """
        if event not in VALID_HOOKS:
            return HookFireResult(event=event, correlation_id=correlation_id or "")

        cid = correlation_id or uuid.uuid4().hex[:12]
        hooks = self._hooks.get(event, [])
        if not hooks:
            return HookFireResult(event=event, correlation_id=cid)

        self._stats["fires"] += 1
        start = time.perf_counter()
        results = []

        for hook in hooks:
            hook_start = time.perf_counter()
            try:
                if inspect.iscoroutinefunction(hook.fn):
                    ret = await hook.fn(correlation_id=cid, **kwargs)
                else:
                    ret = hook.fn(correlation_id=cid, **kwargs)

                ctx_injection = None
                if event == "pre_llm_call" and isinstance(ret, dict):
                    ctx_injection = ret

                duration = (time.perf_counter() - hook_start) * 1000
                results.append(HookResult(
                    hook_name=event,
                    plugin_name=hook.plugin_name,
                    success=True,
                    duration_ms=duration,
                    context_injection=ctx_injection,
                ))
                self._stats["successes"] += 1
            except Exception as e:
                duration = (time.perf_counter() - hook_start) * 1000
                logger.warning(
                    "Hook %s/%s failed: %s (%.1fms)",
                    event, hook.plugin_name, e, duration,
                )
                results.append(HookResult(
                    hook_name=event,
                    plugin_name=hook.plugin_name,
                    success=False,
                    duration_ms=duration,
                    error=str(e),
                ))
                self._stats["failures"] += 1

        total = (time.perf_counter() - start) * 1000
        return HookFireResult(
            event=event,
            correlation_id=cid,
            results=results,
            total_ms=total,
        )

    def registered_count(self, event: Optional[str] = None) -> int:
        """Count registered hooks, optionally for a specific event."""
        if event:
            return len(self._hooks.get(event, []))
        return sum(len(hooks) for hooks in self._hooks.values())

    def stats(self) -> Dict[str, Any]:
        """Return hook execution stats."""
        return {
            **self._stats,
            "registered": self.registered_count(),
            "by_event": {e: len(hooks) for e, hooks in self._hooks.items() if hooks},
        }

    def clear(self, event: Optional[str] = None) -> None:
        """Clear all hooks, or hooks for a specific event."""
        if event:
            self._hooks[event] = []
        else:
            self._hooks = {e: [] for e in VALID_HOOKS}
