"""
Unified tool registry -- consolidates MCP, shell, and skill tools into one catalog.

Inspired by RhysSullivan/executor plugin architecture:
each tool source implements ToolSourceManager protocol,
registry aggregates all sources into a single queryable catalog.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Callable,
    Coroutine,
    Dict,
    List,
    Optional,
    Protocol,
    runtime_checkable,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums & data classes
# ---------------------------------------------------------------------------

class ToolCategory(Enum):
    MCP = "mcp"
    SHELL = "shell"
    SKILL = "skill"
    NATIVE = "native"  # Python functions registered directly


@dataclass
class ToolDefinition:
    """Universal tool representation across all sources."""

    name: str                                   # Fully qualified: "mcp:github:issues_list"
    display_name: str                           # Human-readable name
    description: str
    category: ToolCategory
    source: str                                 # Source manager name
    input_schema: Dict[str, Any]                # JSON Schema for parameters
    requires_approval: bool = False
    trust_level: int = 2                        # 1=observe, 2=suggest, 3=act, 4=autonomous
    cost_estimate: Optional[float] = None       # Estimated cost per invocation
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """Universal tool execution result."""

    success: bool
    output: Any
    error: Optional[str] = None
    execution_time_ms: float = 0
    audit_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class ToolSourceManager(Protocol):
    """Protocol for tool source managers -- each source implements this."""

    @property
    def name(self) -> str:
        """Unique name for this source (e.g. 'mcp', 'shell', 'skill')."""
        ...

    @property
    def category(self) -> ToolCategory:
        """Category of tools this source provides."""
        ...

    async def list_tools(self) -> List[ToolDefinition]:
        """Return all tools currently available from this source."""
        ...

    async def call_tool(self, tool_name: str, args: Dict[str, Any]) -> ToolResult:
        """Execute a tool by its *local* name (without source prefix)."""
        ...

    async def refresh(self) -> int:
        """Re-discover tools from the underlying system. Return new count."""
        ...

    def is_available(self) -> bool:
        """Return True if this source is operational right now."""
        ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# Type alias for native tool handlers
NativeHandler = Callable[..., Any]


class UnifiedToolRegistry:
    """Aggregates all tool sources into a single queryable catalog."""

    def __init__(self) -> None:
        self._sources: Dict[str, ToolSourceManager] = {}
        self._tools: Dict[str, ToolDefinition] = {}       # Cached tool catalog
        self._runtime_tools: Dict[str, ToolDefinition] = {}  # Ephemeral (not persisted)
        self._runtime_handlers: Dict[str, NativeHandler] = {}

    # -- Source management --------------------------------------------------

    def register_source(self, source: ToolSourceManager) -> None:
        """Register a tool source manager."""
        if source.name in self._sources:
            logger.warning("Replacing existing source: %s", source.name)
        self._sources[source.name] = source
        logger.info("Registered tool source: %s", source.name)

    def unregister_source(self, name: str) -> None:
        """Remove a source and its cached tools."""
        if name not in self._sources:
            logger.warning("Source not registered: %s", name)
            return
        del self._sources[name]
        # Purge cached tools from this source
        self._tools = {
            k: v for k, v in self._tools.items() if v.source != name
        }
        logger.info("Unregistered tool source: %s", name)

    # -- Refresh ------------------------------------------------------------

    async def refresh_all(self) -> Dict[str, int]:
        """Refresh all sources and rebuild the tool catalog. Returns per-source counts."""
        counts: Dict[str, int] = {}
        new_tools: Dict[str, ToolDefinition] = {}

        for src_name, source in self._sources.items():
            if not source.is_available():
                logger.debug("Source %s unavailable, skipping refresh", src_name)
                counts[src_name] = 0
                continue
            try:
                count = await source.refresh()
                tools = await source.list_tools()
                for tool in tools:
                    new_tools[tool.name] = tool
                counts[src_name] = count
            except Exception:
                logger.exception("Failed to refresh source: %s", src_name)
                counts[src_name] = 0

        self._tools = new_tools
        logger.info(
            "Refreshed %d sources, %d tools total",
            len(counts),
            sum(counts.values()),
        )
        return counts

    # -- Query --------------------------------------------------------------

    def list_tools(
        self,
        category: Optional[ToolCategory] = None,
        tags: Optional[List[str]] = None,
    ) -> List[ToolDefinition]:
        """List tools, optionally filtered by category and/or tags."""
        all_tools = {**self._tools, **self._runtime_tools}
        results: List[ToolDefinition] = []

        for tool in all_tools.values():
            if category is not None and tool.category != category:
                continue
            if tags:
                if not any(t in tool.tags for t in tags):
                    continue
            results.append(tool)

        return results

    def get_tool(self, qualified_name: str) -> Optional[ToolDefinition]:
        """Look up a tool by its fully-qualified name."""
        if qualified_name in self._runtime_tools:
            return self._runtime_tools[qualified_name]
        return self._tools.get(qualified_name)

    # -- Execution ----------------------------------------------------------

    async def call_tool(self, qualified_name: str, args: Dict[str, Any]) -> ToolResult:
        """
        Execute a tool by fully-qualified name.

        Resolves the owning source from the name prefix and delegates.
        Runtime (native) tools are handled directly.
        """
        start = time.monotonic()

        # Check runtime tools first
        if qualified_name in self._runtime_handlers:
            return await self._call_native(qualified_name, args, start)

        tool = self.get_tool(qualified_name)
        if tool is None:
            return ToolResult(
                success=False,
                output=None,
                error=f"Tool not found: {qualified_name}",
            )

        source = self._sources.get(tool.source)
        if source is None:
            return ToolResult(
                success=False,
                output=None,
                error=f"Source not available: {tool.source}",
            )

        # Strip the source prefix to get the local tool name the source expects.
        # Convention: qualified names are "{category}:{local_name}" where
        # local_name may itself contain colons (e.g. "mcp:github:issues_list").
        prefix = f"{tool.category.value}:"
        local_name = qualified_name[len(prefix):] if qualified_name.startswith(prefix) else qualified_name

        try:
            result = await source.call_tool(local_name, args)
            result.execution_time_ms = (time.monotonic() - start) * 1000
            return result
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            logger.exception("Tool call failed: %s", qualified_name)
            return ToolResult(
                success=False,
                output=None,
                error=str(exc),
                execution_time_ms=elapsed,
            )

    # -- Runtime (native) tools ---------------------------------------------

    def register_runtime_tool(
        self,
        tool: ToolDefinition,
        handler: NativeHandler,
    ) -> None:
        """Register an ephemeral native Python tool (not persisted across restarts)."""
        self._runtime_tools[tool.name] = tool
        self._runtime_handlers[tool.name] = handler
        logger.info("Registered runtime tool: %s", tool.name)

    def unregister_runtime_tool(self, name: str) -> None:
        """Remove an ephemeral tool."""
        self._runtime_tools.pop(name, None)
        self._runtime_handlers.pop(name, None)

    async def _call_native(
        self, name: str, args: Dict[str, Any], start: float
    ) -> ToolResult:
        """Execute a native Python handler."""
        handler = self._runtime_handlers[name]
        try:
            if asyncio.iscoroutinefunction(handler):
                output = await handler(**args)
            else:
                output = await asyncio.to_thread(handler, **args)
            return ToolResult(
                success=True,
                output=output,
                execution_time_ms=(time.monotonic() - start) * 1000,
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                output=None,
                error=str(exc),
                execution_time_ms=(time.monotonic() - start) * 1000,
            )

    # -- LLM integration ----------------------------------------------------

    def get_tools_for_llm(self) -> List[Dict[str, Any]]:
        """
        Return the tool catalog in OpenAI function-calling format.

        Each entry is a dict with ``type: "function"`` and a nested
        ``function`` object containing ``name``, ``description``, and
        ``parameters`` (JSON Schema).
        """
        all_tools = {**self._tools, **self._runtime_tools}
        result: List[Dict[str, Any]] = []

        for tool in all_tools.values():
            # OpenAI function names must be alphanumeric + underscores
            safe_name = tool.name.replace(":", "_").replace("-", "_")
            result.append({
                "type": "function",
                "function": {
                    "name": safe_name,
                    "description": f"[{tool.category.value}] {tool.description}",
                    "parameters": tool.input_schema or {"type": "object", "properties": {}},
                },
            })

        return result

    # -- Properties ---------------------------------------------------------

    @property
    def sources(self) -> List[str]:
        """Names of all registered sources."""
        return list(self._sources.keys())

    @property
    def tool_count(self) -> int:
        """Total number of tools across all sources + runtime."""
        return len(self._tools) + len(self._runtime_tools)

    def get_status(self) -> Dict[str, Any]:
        """Diagnostic summary of the registry."""
        return {
            "sources": {
                name: {
                    "category": src.category.value,
                    "available": src.is_available(),
                }
                for name, src in self._sources.items()
            },
            "tools_cached": len(self._tools),
            "tools_runtime": len(self._runtime_tools),
            "tools_total": self.tool_count,
        }
