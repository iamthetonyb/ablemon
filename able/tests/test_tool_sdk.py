"""Tests for the callable tool catalog SDK (ToolRegistry.generate_callable_sdk)."""

import asyncio
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from able.core.gateway.tool_registry import ToolRegistry, ToolContext


def _make_registry_with_tools():
    """Create a registry with a couple of test tools."""
    registry = ToolRegistry()

    async def handle_search(args, context):
        return f"searched: {args.get('query', '')}"

    async def handle_status(args, context):
        return "system ok"

    registry.register(
        name="web_search",
        definition={
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "num_results": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            },
        },
        handler=handle_search,
        display_name="Web Search",
        description="Search the web",
        category="research",
    )

    registry.register(
        name="system_status",
        definition={
            "type": "function",
            "function": {
                "name": "system_status",
                "description": "Get system status",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        handler=handle_status,
        display_name="System Status",
        description="Get system status",
        category="system",
    )

    return registry


def test_sdk_namespace_shape():
    registry = _make_registry_with_tools()
    sdk = registry.generate_callable_sdk()
    assert isinstance(sdk, SimpleNamespace)
    assert hasattr(sdk, "web_search")
    assert hasattr(sdk, "system_status")
    assert callable(sdk.web_search)
    assert callable(sdk.system_status)


@pytest.mark.asyncio
async def test_callable_dispatch():
    registry = _make_registry_with_tools()
    sdk = registry.generate_callable_sdk()
    result = await sdk.web_search(query="test")
    assert "searched: test" in result


@pytest.mark.asyncio
async def test_callable_no_args():
    registry = _make_registry_with_tools()
    sdk = registry.generate_callable_sdk()
    result = await sdk.system_status()
    assert result == "system ok"


@pytest.mark.asyncio
async def test_approval_required_raises():
    registry = ToolRegistry()

    async def handle_deploy(args, context):
        return "deployed"

    registry.register(
        name="deploy_prod",
        definition={
            "type": "function",
            "function": {
                "name": "deploy_prod",
                "description": "Deploy to production",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        handler=handle_deploy,
        display_name="Deploy",
        description="Deploy to production",
        requires_approval=True,
    )

    sdk = registry.generate_callable_sdk()

    with pytest.raises(PermissionError, match="requires approval"):
        await sdk.deploy_prod()


def test_sdk_stored_on_registry():
    registry = _make_registry_with_tools()
    sdk = registry.generate_callable_sdk()
    assert registry.sdk is sdk
