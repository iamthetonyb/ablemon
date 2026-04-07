"""
MCP SDK Codegen — typed Python callable wrappers from MCP tool schemas.

At MCPBridge.connect_all() time, inspects each MCPTool.input_schema
and generates typed Python callable wrappers. Returns a namespace object
where sdk.<server>.<tool>(kwarg=val) calls through to bridge.call_tool()
with argument validation.

Inspired by RhysSullivan/executor pattern: "detect tools at connect time,
extract schemas, generate callable wrappers, code-gen into the execution
environment."
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .bridge import MCPBridge, MCPTool

logger = logging.getLogger(__name__)

# JSON Schema type → Python annotation string (for docstrings)
_TYPE_MAP = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "array": "list",
    "object": "dict",
    "null": "None",
}


def _python_type_hint(schema: Dict[str, Any]) -> str:
    """Map a JSON Schema type to a Python type hint string."""
    json_type = schema.get("type", "any")
    if isinstance(json_type, list):
        hints = [_TYPE_MAP.get(t, "Any") for t in json_type]
        return " | ".join(hints)
    return _TYPE_MAP.get(json_type, "Any")


def _make_callable(
    tool_full_name: str,
    tool_description: str,
    input_schema: Dict[str, Any],
    bridge: "MCPBridge",
):
    """
    Generate a callable function for a single MCP tool.

    The generated function validates required args before making
    the network call (fast-fail on missing required params).
    """
    properties = input_schema.get("properties", {})
    required = set(input_schema.get("required", []))

    # Build docstring with param signatures
    param_lines = []
    for param_name, param_schema in properties.items():
        type_hint = _python_type_hint(param_schema)
        req_marker = " (required)" if param_name in required else ""
        desc = param_schema.get("description", "")
        param_lines.append(f"    {param_name}: {type_hint}{req_marker} — {desc}")

    docstring = tool_description or "MCP tool"
    if param_lines:
        docstring += "\n\nArgs:\n" + "\n".join(param_lines)

    async def tool_callable(**kwargs) -> Any:
        # Validate required args
        missing = required - set(kwargs.keys())
        if missing:
            raise TypeError(
                f"{tool_full_name}() missing required arguments: "
                + ", ".join(sorted(missing))
            )
        # Validate no unknown args
        unknown = set(kwargs.keys()) - set(properties.keys())
        if unknown:
            logger.warning(
                "Unknown args for %s: %s (passing through)",
                tool_full_name,
                ", ".join(sorted(unknown)),
            )
        result = await bridge.call_tool(tool_full_name, kwargs)
        if not result.success:
            raise RuntimeError(
                f"MCP tool {tool_full_name} failed: {result.error}"
            )
        return result.content

    tool_callable.__doc__ = docstring
    tool_callable.__name__ = tool_full_name.replace(":", "_")
    tool_callable.__qualname__ = tool_full_name.replace(":", "_")
    return tool_callable


class MCPSDKGenerator:
    """
    Generate a typed callable SDK namespace from discovered MCP tools.

    Usage:
        sdk = MCPSDKGenerator.generate(tools, bridge)
        result = await sdk.neon.run_sql(sql="SELECT 1", project_id="abc")
    """

    @staticmethod
    def generate(
        tools: List["MCPTool"],
        bridge: "MCPBridge",
    ) -> SimpleNamespace:
        """
        Generate SDK namespace from a list of MCPTools.

        Returns a SimpleNamespace where each server is a sub-namespace
        and each tool is a callable async function.

        Example:
            sdk.neon.run_sql(sql="...", project_id="...")
            sdk.github.create_issue(title="...", repo="...")
        """
        servers: Dict[str, Dict[str, Any]] = {}

        for tool in tools:
            server_name = tool.server
            if server_name not in servers:
                servers[server_name] = {}

            full_name = f"{server_name}:{tool.name}"
            callable_fn = _make_callable(
                tool_full_name=full_name,
                tool_description=tool.description,
                input_schema=tool.input_schema,
                bridge=bridge,
            )
            # Use tool.name as the attribute (not full_name)
            safe_name = tool.name.replace("-", "_").replace(".", "_")
            servers[server_name][safe_name] = callable_fn

        # Build nested namespace: sdk.<server>.<tool>
        sdk = SimpleNamespace()
        for server_name, tool_map in servers.items():
            safe_server = server_name.replace("-", "_").replace(".", "_")
            server_ns = SimpleNamespace(**tool_map)
            setattr(sdk, safe_server, server_ns)

        tool_count = sum(len(t) for t in servers.values())
        logger.info(
            "MCP SDK generated: %d tools across %d servers",
            tool_count,
            len(servers),
        )

        return sdk
