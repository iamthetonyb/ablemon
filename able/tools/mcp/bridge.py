"""
MCP Tool Bridge - Model Context Protocol integration.

Connects to MCP servers for external tool integrations.
Each integration runs as a separate process with explicit config.

MCP Spec: https://modelcontextprotocol.io/

Supports:
- stdio transport (subprocess)
- SSE transport (HTTP)
- Tool discovery and invocation
- Resource access
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, AsyncIterator

logger = logging.getLogger(__name__)

# Try to import httpx for SSE transport
try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False


class MCPTransport(Enum):
    STDIO = "stdio"
    SSE = "sse"


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server"""
    name: str
    command: str                    # Command to run (for stdio)
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    transport: MCPTransport = MCPTransport.STDIO
    url: str = ""                   # For SSE transport
    enabled: bool = True
    timeout: float = 30.0


@dataclass
class MCPTool:
    """A tool exposed by an MCP server"""
    name: str
    description: str
    server: str
    input_schema: Dict = field(default_factory=dict)
    metadata: Dict = field(default_factory=dict)


@dataclass
class MCPResource:
    """A resource exposed by an MCP server"""
    uri: str
    name: str
    description: str
    server: str
    mime_type: str = "text/plain"


@dataclass
class MCPToolResult:
    """Result from invoking an MCP tool"""
    success: bool
    content: Any
    error: Optional[str] = None
    metadata: Dict = field(default_factory=dict)


class MCPStdioConnection:
    """
    Connection to an MCP server via stdio.

    Spawns a subprocess and communicates via JSON-RPC over stdin/stdout.
    """

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self._process: Optional[subprocess.Popen] = None
        self._request_id = 0
        self._pending: Dict[int, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None

    async def connect(self) -> bool:
        """Start the MCP server process"""
        try:
            env = {**os.environ, **self.config.env}

            self._process = subprocess.Popen(
                [self.config.command] + self.config.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                bufsize=0,
            )

            # Start reader task
            self._reader_task = asyncio.create_task(self._read_responses())

            # Initialize connection
            result = await self._send_request("initialize", {
                "protocolVersion": "0.1.0",
                "capabilities": {},
                "clientInfo": {
                    "name": "ABLE",
                    "version": "2.0"
                }
            })

            if result and result.get("protocolVersion"):
                logger.info(f"MCP server {self.config.name} connected")
                return True

            return False

        except Exception as e:
            logger.error(f"Failed to connect to MCP server {self.config.name}: {e}")
            return False

    async def disconnect(self):
        """Stop the MCP server process"""
        if self._reader_task:
            self._reader_task.cancel()

        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()

    async def _send_request(self, method: str, params: Dict = None) -> Optional[Dict]:
        """Send a JSON-RPC request and wait for response"""
        if not self._process or self._process.poll() is not None:
            return None

        self._request_id += 1
        request_id = self._request_id

        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {}
        }

        future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future

        try:
            message = json.dumps(request) + "\n"
            self._process.stdin.write(message.encode())
            self._process.stdin.flush()

            result = await asyncio.wait_for(future, timeout=self.config.timeout)
            return result

        except asyncio.TimeoutError:
            logger.warning(f"Request {method} timed out")
            self._pending.pop(request_id, None)
            return None
        except Exception as e:
            logger.error(f"Request failed: {e}")
            self._pending.pop(request_id, None)
            return None

    async def _read_responses(self):
        """Read responses from the MCP server"""
        while self._process and self._process.poll() is None:
            try:
                line = await asyncio.get_event_loop().run_in_executor(
                    None, self._process.stdout.readline
                )

                if not line:
                    await asyncio.sleep(0.01)
                    continue

                response = json.loads(line.decode())

                if "id" in response:
                    request_id = response["id"]
                    if request_id in self._pending:
                        future = self._pending.pop(request_id)
                        if "error" in response:
                            future.set_exception(Exception(response["error"]))
                        else:
                            future.set_result(response.get("result"))

            except json.JSONDecodeError:
                continue
            except Exception as e:
                if not isinstance(e, asyncio.CancelledError):
                    logger.debug(f"Reader error: {e}")
                break

    async def list_tools(self) -> List[MCPTool]:
        """List available tools from this server"""
        result = await self._send_request("tools/list", {})
        if not result:
            return []

        tools = []
        for tool_data in result.get("tools", []):
            tools.append(MCPTool(
                name=tool_data["name"],
                description=tool_data.get("description", ""),
                server=self.config.name,
                input_schema=tool_data.get("inputSchema", {}),
            ))

        return tools

    async def list_resources(self) -> List[MCPResource]:
        """List available resources from this server"""
        result = await self._send_request("resources/list", {})
        if not result:
            return []

        resources = []
        for res_data in result.get("resources", []):
            resources.append(MCPResource(
                uri=res_data["uri"],
                name=res_data.get("name", res_data["uri"]),
                description=res_data.get("description", ""),
                server=self.config.name,
                mime_type=res_data.get("mimeType", "text/plain"),
            ))

        return resources

    async def call_tool(self, name: str, arguments: Dict) -> MCPToolResult:
        """Call a tool on this server"""
        result = await self._send_request("tools/call", {
            "name": name,
            "arguments": arguments
        })

        if result is None:
            return MCPToolResult(
                success=False,
                content=None,
                error="Request failed or timed out"
            )

        if "error" in result:
            return MCPToolResult(
                success=False,
                content=None,
                error=str(result["error"])
            )

        return MCPToolResult(
            success=True,
            content=result.get("content", []),
            metadata=result.get("metadata", {})
        )

    async def read_resource(self, uri: str) -> Optional[str]:
        """Read a resource from this server"""
        result = await self._send_request("resources/read", {"uri": uri})
        if result and "contents" in result:
            contents = result["contents"]
            if contents and len(contents) > 0:
                return contents[0].get("text", "")
        return None


class MCPBridge:
    """
    Bridge to multiple MCP servers.

    Aggregates tools and resources from all connected servers.
    Exposes unified interface for tool discovery and invocation.

    Usage:
        bridge = MCPBridge()
        bridge.load_config("mcp_servers.json")
        await bridge.connect_all()

        tools = await bridge.list_all_tools()
        result = await bridge.call_tool("server:tool_name", {"arg": "value"})
    """

    def __init__(self, config_path: Path = None):
        self.config_path = config_path
        self.servers: Dict[str, MCPServerConfig] = {}
        self.connections: Dict[str, MCPStdioConnection] = {}
        self._tools_cache: Dict[str, MCPTool] = {}
        self._resources_cache: Dict[str, MCPResource] = {}
        self.sdk = None  # Populated by generate_sdk() after connect_all()

    def load_config(self, path: Path = None):
        """Load MCP server configurations from JSON file"""
        config_path = path or self.config_path
        if not config_path or not Path(config_path).exists():
            logger.warning(f"MCP config not found: {config_path}")
            return

        with open(config_path) as f:
            config = json.load(f)

        for name, server_config in config.get("mcpServers", {}).items():
            transport = MCPTransport(server_config.get("transport", "stdio"))

            self.servers[name] = MCPServerConfig(
                name=name,
                command=server_config.get("command", ""),
                args=server_config.get("args", []),
                env=server_config.get("env", {}),
                transport=transport,
                url=server_config.get("url", ""),
                enabled=server_config.get("enabled", True),
                timeout=server_config.get("timeout", 30.0),
            )

        logger.info(f"Loaded {len(self.servers)} MCP server configs")

    def add_server(self, config: MCPServerConfig):
        """Add a server configuration"""
        self.servers[config.name] = config

    async def connect_all(self) -> Dict[str, bool]:
        """Connect to all enabled servers"""
        results = {}

        for name, config in self.servers.items():
            if not config.enabled:
                results[name] = False
                continue

            if config.transport == MCPTransport.STDIO:
                conn = MCPStdioConnection(config)
                success = await conn.connect()
                if success:
                    self.connections[name] = conn
                results[name] = success
            else:
                logger.warning(f"SSE transport not yet implemented for {name}")
                results[name] = False

        # Refresh tool cache
        await self._refresh_cache()

        # Generate typed callable SDK from discovered tools
        self.generate_sdk()

        return results

    async def disconnect_all(self):
        """Disconnect from all servers"""
        for conn in self.connections.values():
            await conn.disconnect()
        self.connections.clear()
        self._tools_cache.clear()
        self._resources_cache.clear()

    async def _refresh_cache(self):
        """Refresh tools and resources cache from all servers"""
        self._tools_cache.clear()
        self._resources_cache.clear()

        for name, conn in self.connections.items():
            try:
                tools = await conn.list_tools()
                for tool in tools:
                    key = f"{name}:{tool.name}"
                    self._tools_cache[key] = tool

                resources = await conn.list_resources()
                for resource in resources:
                    key = f"{name}:{resource.uri}"
                    self._resources_cache[key] = resource

            except Exception as e:
                logger.warning(f"Failed to cache tools from {name}: {e}")

    async def list_all_tools(self) -> List[MCPTool]:
        """List all tools from all connected servers"""
        return list(self._tools_cache.values())

    async def list_all_resources(self) -> List[MCPResource]:
        """List all resources from all connected servers"""
        return list(self._resources_cache.values())

    async def call_tool(self, full_name: str, arguments: Dict) -> MCPToolResult:
        """
        Call a tool by its full name (server:tool_name).

        Args:
            full_name: Tool name in format "server:tool_name"
            arguments: Tool arguments

        Returns:
            MCPToolResult with success status and content
        """
        if ":" not in full_name:
            return MCPToolResult(
                success=False,
                content=None,
                error=f"Invalid tool name format. Use 'server:tool_name'"
            )

        server_name, tool_name = full_name.split(":", 1)

        if server_name not in self.connections:
            return MCPToolResult(
                success=False,
                content=None,
                error=f"Server '{server_name}' not connected"
            )

        conn = self.connections[server_name]
        return await conn.call_tool(tool_name, arguments)

    async def read_resource(self, full_uri: str) -> Optional[str]:
        """
        Read a resource by its full URI (server:uri).
        """
        if ":" not in full_uri:
            return None

        server_name, uri = full_uri.split(":", 1)

        if server_name not in self.connections:
            return None

        conn = self.connections[server_name]
        return await conn.read_resource(uri)

    def get_tools_for_llm(self) -> List[Dict]:
        """Get tools in LLM-compatible format (OpenAI function calling)"""
        tools = []

        for full_name, tool in self._tools_cache.items():
            tools.append({
                "type": "function",
                "function": {
                    "name": full_name.replace(":", "_"),  # LLM-safe name
                    "description": f"[MCP:{tool.server}] {tool.description}",
                    "parameters": tool.input_schema,
                }
            })

        return tools

    def generate_sdk(self):
        """Generate typed callable SDK from all discovered MCP tools.

        Called automatically after connect_all(). Can also be called
        manually to regenerate after tool changes.

        Returns the SDK namespace (also stored as self.sdk).
        """
        try:
            from .sdk_gen import MCPSDKGenerator
            tools = list(self._tools_cache.values())
            if tools:
                self.sdk = MCPSDKGenerator.generate(tools, self)
                logger.info("MCP SDK generated with %d tools", len(tools))
            else:
                self.sdk = None
                logger.debug("No MCP tools discovered — SDK not generated")
        except Exception as e:
            logger.warning("MCP SDK generation failed: %s", e)
            self.sdk = None
        return self.sdk

    def get_status(self) -> Dict[str, Any]:
        """Get bridge status"""
        return {
            "servers_configured": len(self.servers),
            "servers_connected": len(self.connections),
            "tools_available": len(self._tools_cache),
            "resources_available": len(self._resources_cache),
            "connections": {
                name: "connected" for name in self.connections.keys()
            }
        }
