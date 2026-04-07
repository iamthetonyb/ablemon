"""
MCP source adapter -- wraps MCPBridge as a ToolSourceManager.

Namespace convention: mcp:{server}:{tool_name}
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..registry import ToolCategory, ToolDefinition, ToolResult, ToolSourceManager

logger = logging.getLogger(__name__)

# Lazy import -- MCPBridge may not be initialised yet
_MCPBridge = None
_MCPTool = None


def _ensure_imports():
    global _MCPBridge, _MCPTool
    if _MCPBridge is None:
        try:
            from able.tools.mcp.bridge import MCPBridge, MCPTool
            _MCPBridge = MCPBridge
            _MCPTool = MCPTool
        except ImportError:
            logger.debug("MCPBridge not importable -- MCP source unavailable")


class MCPSource:
    """
    ToolSourceManager adapter for the MCP bridge.

    Wraps ``MCPBridge.list_all_tools()`` and ``MCPBridge.call_tool()``
    to present MCP server tools through the unified registry.
    """

    def __init__(self, bridge: Optional[Any] = None) -> None:
        """
        Args:
            bridge: An existing ``MCPBridge`` instance.  If ``None`` the
                    source will report itself as unavailable until one is
                    injected via ``set_bridge()``.
        """
        _ensure_imports()
        self._bridge = bridge
        self._tools_cache: List[ToolDefinition] = []

    # -- Protocol properties -----------------------------------------------

    @property
    def name(self) -> str:
        return "mcp"

    @property
    def category(self) -> ToolCategory:
        return ToolCategory.MCP

    # -- Lifecycle ----------------------------------------------------------

    def set_bridge(self, bridge: Any) -> None:
        """Inject or replace the underlying MCPBridge."""
        self._bridge = bridge

    def is_available(self) -> bool:
        if self._bridge is None:
            return False
        # Bridge is usable when it has at least one active connection
        return bool(getattr(self._bridge, "connections", None))

    # -- Tool discovery -----------------------------------------------------

    async def list_tools(self) -> List[ToolDefinition]:
        if self._tools_cache:
            return list(self._tools_cache)
        await self.refresh()
        return list(self._tools_cache)

    async def refresh(self) -> int:
        """
        Re-discover tools from the MCP bridge.

        Calls ``bridge.connect_all()`` if there are no active connections,
        then converts each ``MCPTool`` into a ``ToolDefinition``.
        """
        if not self._bridge:
            self._tools_cache = []
            return 0

        # If the bridge has servers configured but no connections, try connecting
        if (
            getattr(self._bridge, "servers", None)
            and not getattr(self._bridge, "connections", None)
        ):
            try:
                await self._bridge.connect_all()
            except Exception:
                logger.exception("MCPBridge.connect_all() failed during refresh")
                self._tools_cache = []
                return 0

        try:
            mcp_tools = await self._bridge.list_all_tools()
        except Exception:
            logger.exception("MCPBridge.list_all_tools() failed")
            self._tools_cache = []
            return 0

        definitions: List[ToolDefinition] = []
        for mcp_tool in mcp_tools:
            qualified = f"mcp:{mcp_tool.server}:{mcp_tool.name}"
            definitions.append(
                ToolDefinition(
                    name=qualified,
                    display_name=mcp_tool.name,
                    description=mcp_tool.description,
                    category=ToolCategory.MCP,
                    source=self.name,
                    input_schema=mcp_tool.input_schema,
                    requires_approval=False,
                    trust_level=3,  # MCP tools are external -- default to ACT
                    tags=["mcp", mcp_tool.server],
                    metadata=mcp_tool.metadata,
                )
            )

        self._tools_cache = definitions
        logger.info("MCP source refreshed: %d tools from %d servers",
                     len(definitions), len(getattr(self._bridge, "connections", {})))
        return len(definitions)

    # -- Execution ----------------------------------------------------------

    async def call_tool(self, tool_name: str, args: Dict[str, Any]) -> ToolResult:
        """
        Execute an MCP tool.

        ``tool_name`` is the local name *without* the ``mcp:`` prefix,
        i.e. ``server:tool_name`` -- which is exactly what
        ``MCPBridge.call_tool()`` expects.
        """
        if not self._bridge:
            return ToolResult(
                success=False,
                output=None,
                error="MCP bridge not initialised",
            )

        if ":" not in tool_name:
            return ToolResult(
                success=False,
                output=None,
                error=f"Invalid MCP tool name (expected 'server:tool'): {tool_name}",
            )

        try:
            mcp_result = await self._bridge.call_tool(tool_name, args)
            return ToolResult(
                success=mcp_result.success,
                output=mcp_result.content,
                error=mcp_result.error,
            )
        except Exception as exc:
            logger.exception("MCP tool call failed: %s", tool_name)
            return ToolResult(
                success=False,
                output=None,
                error=str(exc),
            )
