"""
MCP (Model Context Protocol) Tool Bridge

Connects ABLE to external MCP servers for tool integrations.
Supports stdio and SSE transports.
"""

from .bridge import MCPBridge, MCPTool, MCPToolResult, MCPResource

__all__ = ["MCPBridge", "MCPTool", "MCPToolResult", "MCPResource"]
