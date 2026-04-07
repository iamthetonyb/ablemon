"""Tests for MCP SDK codegen — typed Python wrappers from MCP tool schemas."""

import asyncio
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from able.tools.mcp.sdk_gen import MCPSDKGenerator, _python_type_hint, _make_callable


# ── Helpers ─────────────────────────────────────────────────────

def _make_tool(name, server, schema, description="test tool"):
    """Create a mock MCPTool."""
    tool = MagicMock()
    tool.name = name
    tool.server = server
    tool.description = description
    tool.input_schema = schema
    return tool


def _make_bridge():
    """Create a mock MCPBridge with a call_tool method."""
    bridge = MagicMock()
    result = MagicMock()
    result.success = True
    result.content = "ok"
    bridge.call_tool = AsyncMock(return_value=result)
    return bridge


# ── Tests ───────────────────────────────────────────────────────


def test_type_mapping_string():
    assert _python_type_hint({"type": "string"}) == "str"


def test_type_mapping_integer():
    assert _python_type_hint({"type": "integer"}) == "int"


def test_type_mapping_array():
    assert _python_type_hint({"type": "array"}) == "list"


def test_type_mapping_union():
    assert _python_type_hint({"type": ["string", "null"]}) == "str | None"


def test_generate_empty_tools():
    bridge = _make_bridge()
    sdk = MCPSDKGenerator.generate([], bridge)
    assert isinstance(sdk, SimpleNamespace)
    assert not vars(sdk)  # Empty namespace


def test_generate_nested_namespace():
    bridge = _make_bridge()
    tools = [
        _make_tool("run_sql", "neon", {
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        }),
        _make_tool("list_tables", "neon", {
            "properties": {},
        }),
    ]
    sdk = MCPSDKGenerator.generate(tools, bridge)
    assert hasattr(sdk, "neon")
    assert hasattr(sdk.neon, "run_sql")
    assert hasattr(sdk.neon, "list_tables")
    assert callable(sdk.neon.run_sql)


@pytest.mark.asyncio
async def test_callable_roundtrip():
    bridge = _make_bridge()
    tools = [
        _make_tool("search", "web", {
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        }),
    ]
    sdk = MCPSDKGenerator.generate(tools, bridge)
    result = await sdk.web.search(query="test", limit=5)
    assert result == "ok"
    bridge.call_tool.assert_called_once_with("web:search", {"query": "test", "limit": 5})


@pytest.mark.asyncio
async def test_required_arg_enforcement():
    bridge = _make_bridge()
    tools = [
        _make_tool("run_sql", "neon", {
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        }),
    ]
    sdk = MCPSDKGenerator.generate(tools, bridge)
    with pytest.raises(TypeError, match="missing required"):
        await sdk.neon.run_sql()  # Missing 'sql'


@pytest.mark.asyncio
async def test_error_result_raises():
    bridge = _make_bridge()
    error_result = MagicMock()
    error_result.success = False
    error_result.error = "table not found"
    bridge.call_tool = AsyncMock(return_value=error_result)

    tools = [
        _make_tool("query", "db", {
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        }),
    ]
    sdk = MCPSDKGenerator.generate(tools, bridge)
    with pytest.raises(RuntimeError, match="table not found"):
        await sdk.db.query(sql="SELECT 1")
