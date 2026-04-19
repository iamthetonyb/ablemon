"""
D9 — MCP Server Mode.

Exposes ABLE as an MCP (Model Context Protocol) server via stdio or
Streamable HTTP. External agents (Claude Desktop, Cursor, etc.) can
connect to ABLE and use its capabilities.

Forked from Hermes v0.6 PR #3795 + InsForge context engineering pattern.

Tools exposed:
    - able_status         — Current system status, provider health, buddy state
    - able_discover       — Structured capability map (InsForge fetch-docs pattern)
    - able_conversations  — List recent conversations
    - able_message        — Send a message through ABLE's gateway
    - able_skills         — List available skills
    - able_memory_search  — Search ABLE's memory system
    - able_events_poll    — Poll for recent events/notifications
    - able_permissions    — List pending approval requests
    - able_config         — Read current routing config
    - able_tool_list      — List registered tools

Usage (stdio):
    python -m able.tools.mcp.able_mcp_server

Integration (Claude Desktop config):
    {
      "mcpServers": {
        "able": {
          "command": "python",
          "args": ["-m", "able.tools.mcp.able_mcp_server"]
        }
      }
    }
"""

from __future__ import annotations

import collections
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── MCP Protocol Types ─────────────────────────────────────────


class MCPMessageType(str, Enum):
    REQUEST = "request"
    RESPONSE = "response"
    NOTIFICATION = "notification"


@dataclass
class MCPToolDef:
    """An MCP tool definition."""
    name: str
    description: str
    input_schema: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPToolResult:
    """Result from an MCP tool call."""
    content: List[Dict[str, Any]] = field(default_factory=list)
    is_error: bool = False

    def text(self, text: str) -> "MCPToolResult":
        self.content.append({"type": "text", "text": text})
        return self

    def error(self, msg: str) -> "MCPToolResult":
        self.is_error = True
        self.content.append({"type": "text", "text": msg})
        return self


@dataclass
class MCPResource:
    """An MCP resource definition."""
    uri: str
    name: str
    description: str = ""
    mime_type: str = "text/plain"


# ── Tool Handlers ──────────────────────────────────────────────


class ABLEToolHandlers:
    """Handlers for ABLE's MCP-exposed tools.

    Each handler receives the tool arguments dict and returns an MCPToolResult.
    Handlers are designed to work independently — no gateway required for
    basic status/discovery.
    """

    # Max events retained in memory to prevent OOM on long-running servers
    MAX_EVENT_LOG_SIZE = 1000

    def __init__(self):
        self._start_time = time.time()
        self._event_log: collections.deque = collections.deque(
            maxlen=self.MAX_EVENT_LOG_SIZE,
        )
        self._gateway: Any = None

    def set_gateway(self, gateway: Any) -> None:
        """Wire in the ABLE gateway for message processing."""
        self._gateway = gateway

    def handle_status(self, args: Dict[str, Any]) -> MCPToolResult:
        """Return current ABLE system status."""
        status = {
            "system": "ABLE",
            "version": "0.4.8",
            "uptime_s": round(time.time() - self._start_time, 1),
            "mode": "mcp_server",
            "providers": self._get_provider_status(),
            "skills_count": self._count_skills(),
        }
        return MCPToolResult().text(json.dumps(status, indent=2))

    def handle_discover(self, args: Dict[str, Any]) -> MCPToolResult:
        """Return structured capability map (InsForge pattern).

        This tool helps agents understand what ABLE can do BEFORE
        making tool calls — reduces hallucinated tool usage.
        """
        capabilities = {
            "name": "ABLE — Autonomous Business & Learning Engine",
            "description": "Multi-provider AI agent with 5-tier routing, "
                          "persistent memory, skill system, and self-improvement.",
            "tools": [
                {
                    "name": "able_status",
                    "category": "system",
                    "description": "Get system health, uptime, provider status",
                },
                {
                    "name": "able_message",
                    "category": "interaction",
                    "description": "Send a message through ABLE's gateway for processing",
                    "input": {"message": "string", "context": "optional dict"},
                },
                {
                    "name": "able_skills",
                    "category": "capabilities",
                    "description": "List available skills with triggers",
                },
                {
                    "name": "able_memory_search",
                    "category": "memory",
                    "description": "Search ABLE's hybrid memory (SQLite + vector)",
                    "input": {"query": "string", "max_results": "int (default 5)"},
                },
                {
                    "name": "able_config",
                    "category": "system",
                    "description": "Read current routing configuration",
                },
                {
                    "name": "able_tool_list",
                    "category": "capabilities",
                    "description": "List all registered tools",
                },
                {
                    "name": "able_events_poll",
                    "category": "events",
                    "description": "Poll recent events and notifications",
                },
                {
                    "name": "able_permissions",
                    "category": "security",
                    "description": "List pending approval requests",
                },
                {
                    "name": "able_skill_route",
                    "category": "capabilities",
                    "description": "Route query to top-K skills (ABLE + CC library)",
                    "input": {"query": "string", "k": "int (default 5)"},
                },
            ],
            "routing_tiers": [
                {"tier": 1, "models": "GPT 5.4 Mini", "complexity": "<0.4"},
                {"tier": 2, "models": "GPT 5.4", "complexity": "0.4-0.7"},
                {"tier": 3, "models": "MiniMax M2.7", "complexity": "background"},
                {"tier": 4, "models": "Claude Opus 4.6", "complexity": ">0.7"},
                {"tier": 5, "models": "Ollama local", "complexity": "offline"},
            ],
            "usage_protocol": (
                "1. Call able_discover first to understand capabilities. "
                "2. Use able_status to check health. "
                "3. Use able_message for processing. "
                "4. Use able_memory_search for context retrieval."
            ),
        }
        return MCPToolResult().text(json.dumps(capabilities, indent=2))

    def handle_message(self, args: Dict[str, Any]) -> MCPToolResult:
        """Process a message through ABLE's gateway."""
        message = args.get("message", "")
        if not message:
            return MCPToolResult().error("Missing 'message' parameter")

        # Log the event
        self._event_log.append({
            "type": "message",
            "content": message[:200],
            "timestamp": time.time(),
        })

        # Use gateway if wired in, otherwise return acknowledgment
        if self._gateway is not None:
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Already in async context, create task
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        result = pool.submit(
                            asyncio.run,
                            self._gateway.process_message(message),
                        ).result(timeout=120)
                else:
                    result = loop.run_until_complete(
                        self._gateway.process_message(message)
                    )
                return MCPToolResult().text(json.dumps({
                    "status": "processed",
                    "response": str(result)[:5000],
                }))
            except Exception as e:
                logger.warning("Gateway processing failed: %s", e)
                return MCPToolResult().text(json.dumps({
                    "status": "error",
                    "error": str(e)[:500],
                }))

        return MCPToolResult().text(json.dumps({
            "status": "received",
            "message_length": len(message),
            "note": "Gateway not wired in. Use set_gateway() for full processing.",
        }))

    def handle_skills(self, args: Dict[str, Any]) -> MCPToolResult:
        """List available ABLE skills."""
        skills = self._load_skill_index()
        return MCPToolResult().text(json.dumps(skills, indent=2))

    def handle_memory_search(self, args: Dict[str, Any]) -> MCPToolResult:
        """Search ABLE's memory system."""
        query = args.get("query", "")
        max_results = args.get("max_results", 5)
        if not query:
            return MCPToolResult().error("Missing 'query' parameter")

        # Attempt to use hybrid memory if available
        results = self._search_memory(query, max_results)
        return MCPToolResult().text(json.dumps(results, indent=2))

    def handle_events_poll(self, args: Dict[str, Any]) -> MCPToolResult:
        """Poll recent events."""
        limit = args.get("limit", 10)
        events = list(self._event_log)[-limit:]
        return MCPToolResult().text(json.dumps(events, indent=2))

    def handle_permissions(self, args: Dict[str, Any]) -> MCPToolResult:
        """List pending approval requests."""
        return MCPToolResult().text(json.dumps({
            "pending": [],
            "note": "No pending approvals in standalone mode",
        }))

    def handle_config(self, args: Dict[str, Any]) -> MCPToolResult:
        """Read current routing configuration."""
        config = self._load_routing_config()
        return MCPToolResult().text(json.dumps(config, indent=2))

    def handle_tool_list(self, args: Dict[str, Any]) -> MCPToolResult:
        """List registered tools."""
        tools = self._load_tool_list()
        return MCPToolResult().text(json.dumps(tools, indent=2))

    def handle_skill_route(self, args: Dict[str, Any]) -> MCPToolResult:
        """Route a query to top-K most relevant skills.

        Cascade: ABLE hybrid memory (when `search_skills` wired — currently
        unimplemented, graceful skip) → standalone
        ~/.claude/tools/skill-router CLI → error. Non-fatal on each miss.
        """
        raw_query = args.get("query", "")
        if not isinstance(raw_query, str) or not raw_query.strip():
            return MCPToolResult().error("Missing or non-string 'query' parameter")
        query = raw_query[:2000]  # hard cap — argv + embedding sanity
        try:
            k = max(1, min(20, int(args.get("k", 5))))
        except (TypeError, ValueError):
            k = 5

        # Try ABLE hybrid memory first (opt-in — index_skills must be wired)
        try:
            from able.memory.hybrid_memory import HybridMemory
            mem = HybridMemory()
            if hasattr(mem, "search_skills"):
                results = mem.search_skills(query, limit=k)
                if results:
                    return MCPToolResult().text(json.dumps({
                        "skills": self._safe_dict(results),
                        "source": "able_hybrid_memory",
                        "query": query[:200],
                        "k": k,
                    }, default=str))
        except Exception as e:
            logger.debug("ABLE skill search unavailable: %s", e)

        # Fallback: standalone skill-router CLI @ ~/.claude/tools/skill-router/cli.py
        try:
            import subprocess
            import sys as _sys
            from pathlib import Path
            cli = Path.home() / ".claude" / "tools" / "skill-router" / "cli.py"
            if cli.exists():
                proc = subprocess.run(
                    [_sys.executable, str(cli), "--query", query, "--k", str(k)],
                    capture_output=True, timeout=10, text=True,
                )
                if proc.returncode == 0 and proc.stdout:
                    try:
                        parsed = json.loads(proc.stdout)
                    except json.JSONDecodeError:
                        parsed = proc.stdout.strip()
                    return MCPToolResult().text(json.dumps({
                        "skills": parsed,
                        "source": "standalone_cli",
                        "query": query[:200],
                        "k": k,
                    }))
                # CLI ran but failed — surface rc + stderr
                err_tail = (proc.stderr or "")[-400:]
                return MCPToolResult().error(
                    f"skill-router CLI returned rc={proc.returncode}: {err_tail}"
                )
        except Exception as e:
            logger.debug("Standalone CLI fallback failed: %s", e)
            return MCPToolResult().error(f"skill routing failed: {type(e).__name__}: {e}")

        return MCPToolResult().error(
            "skill routing unavailable — ABLE memory not indexed + CLI missing @ "
            "~/.claude/tools/skill-router/cli.py"
        )

    # ── Internal helpers ───────────────────────────────────────

    def _get_provider_status(self) -> Dict[str, str]:
        try:
            from able.core.routing.provider_registry import ProviderRegistry
            reg = ProviderRegistry.from_yaml("config/routing_config.yaml")
            return {p.name: "configured" for p in reg.all_providers()}
        except Exception:
            return {"status": "unavailable"}

    def _count_skills(self) -> int:
        try:
            from pathlib import Path
            index = Path("able/skills/SKILL_INDEX.yaml")
            if index.exists():
                import yaml
                data = yaml.safe_load(index.read_text())
                return len(data.get("skills", []))
        except Exception:
            pass
        return 0

    def _load_skill_index(self) -> Dict[str, Any]:
        try:
            from pathlib import Path
            index = Path("able/skills/SKILL_INDEX.yaml")
            if index.exists():
                import yaml
                return yaml.safe_load(index.read_text())
        except Exception:
            pass
        return {"skills": [], "note": "Skill index not available"}

    def _search_memory(self, query: str, max_results: int) -> Dict[str, Any]:
        try:
            from able.memory.hybrid_memory import HybridMemory
            mem = HybridMemory()
            results = mem.search(query, limit=max_results)
            # Safe serialization: convert any non-JSON objects to strings
            serializable = json.loads(
                json.dumps(
                    [self._safe_dict(r) for r in results],
                    default=str,
                )
            )
            return {"results": serializable, "count": len(serializable)}
        except Exception:
            return {"results": [], "note": "Memory system not available"}

    @staticmethod
    def _safe_dict(obj: Any) -> Any:
        """Recursively convert an object to JSON-safe dict."""
        if isinstance(obj, (str, int, float, bool, type(None))):
            return obj
        if isinstance(obj, dict):
            return {k: ABLEToolHandlers._safe_dict(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [ABLEToolHandlers._safe_dict(v) for v in obj]
        if hasattr(obj, "__dict__"):
            return {
                k: ABLEToolHandlers._safe_dict(v)
                for k, v in obj.__dict__.items()
                if not k.startswith("_")
            }
        return str(obj)

    def _load_routing_config(self) -> Dict[str, Any]:
        try:
            from pathlib import Path
            import yaml
            cfg = Path("config/routing_config.yaml")
            if cfg.exists():
                return yaml.safe_load(cfg.read_text())
        except Exception:
            pass
        return {"note": "Routing config not available"}

    def _load_tool_list(self) -> Dict[str, Any]:
        try:
            from pathlib import Path
            manifest = Path("tools/manifest.md")
            if manifest.exists():
                return {"manifest": manifest.read_text()[:2000]}
        except Exception:
            pass
        return {"tools": [], "note": "Tool manifest not available"}


# ── MCP Server ─────────────────────────────────────────────────


# Tool definitions for the MCP protocol
TOOL_DEFINITIONS: List[MCPToolDef] = [
    MCPToolDef(
        name="able_status",
        description="Get ABLE system status including uptime, provider health, and skill count.",
        input_schema={
            "type": "object",
            "properties": {},
        },
    ),
    MCPToolDef(
        name="able_discover",
        description="Get structured capability map of ABLE's tools, routing tiers, and usage protocol. Call this first to understand what ABLE can do.",
        input_schema={
            "type": "object",
            "properties": {},
        },
    ),
    MCPToolDef(
        name="able_message",
        description="Send a message through ABLE's gateway for multi-tier processing.",
        input_schema={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "The message to process"},
                "context": {"type": "object", "description": "Optional context dict"},
            },
            "required": ["message"],
        },
    ),
    MCPToolDef(
        name="able_skills",
        description="List all available ABLE skills with triggers and descriptions.",
        input_schema={"type": "object", "properties": {}},
    ),
    MCPToolDef(
        name="able_memory_search",
        description="Search ABLE's hybrid memory system (SQLite + vector).",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "description": "Max results", "default": 5},
            },
            "required": ["query"],
        },
    ),
    MCPToolDef(
        name="able_events_poll",
        description="Poll recent events and notifications.",
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max events", "default": 10},
            },
        },
    ),
    MCPToolDef(
        name="able_permissions",
        description="List pending approval requests.",
        input_schema={"type": "object", "properties": {}},
    ),
    MCPToolDef(
        name="able_config",
        description="Read current routing configuration.",
        input_schema={"type": "object", "properties": {}},
    ),
    MCPToolDef(
        name="able_tool_list",
        description="List all registered tools.",
        input_schema={"type": "object", "properties": {}},
    ),
    MCPToolDef(
        name="able_skill_route",
        description=(
            "Route a query to the top-K most relevant skills across ABLE + CC "
            "skill libraries. Returns ranked skills by semantic/BM25 match. "
            "Use before invoking a skill to pick the right one."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "User intent to route"},
                "k": {"type": "integer", "description": "Top K skills", "default": 5},
            },
            "required": ["query"],
        },
    ),
]


class ABLEMCPServer:
    """MCP Server exposing ABLE's capabilities via JSON-RPC over stdio.

    Implements the MCP specification:
    - initialize / initialized handshake
    - tools/list → returns tool definitions
    - tools/call → dispatches to handlers
    - resources/list → returns available resources

    Usage:
        server = ABLEMCPServer()
        server.run_stdio()  # Blocks, reads stdin, writes stdout
    """

    PROTOCOL_VERSION = "2024-11-05"
    SERVER_NAME = "able"
    SERVER_VERSION = "0.4.8"

    def __init__(self):
        self._handlers = ABLEToolHandlers()
        self._initialized = False
        self._tool_map: Dict[str, Callable] = {
            "able_status": self._handlers.handle_status,
            "able_discover": self._handlers.handle_discover,
            "able_message": self._handlers.handle_message,
            "able_skills": self._handlers.handle_skills,
            "able_memory_search": self._handlers.handle_memory_search,
            "able_events_poll": self._handlers.handle_events_poll,
            "able_permissions": self._handlers.handle_permissions,
            "able_config": self._handlers.handle_config,
            "able_tool_list": self._handlers.handle_tool_list,
            "able_skill_route": self._handlers.handle_skill_route,
        }

    def set_gateway(self, gateway: Any) -> None:
        """Wire in the ABLE gateway for full message processing."""
        self._gateway = gateway
        self._handlers.set_gateway(gateway)

    def handle_request(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Handle a single JSON-RPC request."""
        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params", {})

        if method == "initialize":
            return self._handle_initialize(req_id, params)
        elif method == "notifications/initialized":
            self._initialized = True
            return None  # Notification, no response
        elif method == "tools/list":
            return self._handle_tools_list(req_id)
        elif method == "tools/call":
            return self._handle_tools_call(req_id, params)
        elif method == "resources/list":
            return self._handle_resources_list(req_id)
        elif method == "ping":
            return self._success(req_id, {})
        else:
            return self._error(req_id, -32601, f"Method not found: {method}")

    def _handle_initialize(
        self, req_id: Any, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle initialize request."""
        return self._success(req_id, {
            "protocolVersion": self.PROTOCOL_VERSION,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
            },
            "serverInfo": {
                "name": self.SERVER_NAME,
                "version": self.SERVER_VERSION,
            },
        })

    def _handle_tools_list(self, req_id: Any) -> Dict[str, Any]:
        """Handle tools/list request."""
        tools = []
        for td in TOOL_DEFINITIONS:
            tools.append({
                "name": td.name,
                "description": td.description,
                "inputSchema": td.input_schema,
            })
        return self._success(req_id, {"tools": tools})

    def _handle_tools_call(
        self, req_id: Any, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle tools/call request."""
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})

        handler = self._tool_map.get(tool_name)
        if not handler:
            return self._error(req_id, -32602, f"Unknown tool: {tool_name}")

        try:
            result = handler(tool_args)
            return self._success(req_id, {
                "content": result.content,
                "isError": result.is_error,
            })
        except Exception as e:
            logger.exception("Tool call failed: %s", tool_name)
            return self._success(req_id, {
                "content": [{"type": "text", "text": f"Error: {e}"}],
                "isError": True,
            })

    def _handle_resources_list(self, req_id: Any) -> Dict[str, Any]:
        """Handle resources/list request."""
        resources = [
            {
                "uri": "able://config/routing",
                "name": "Routing Configuration",
                "description": "Current provider routing config",
                "mimeType": "application/yaml",
            },
            {
                "uri": "able://skills/index",
                "name": "Skill Index",
                "description": "All registered ABLE skills",
                "mimeType": "application/yaml",
            },
        ]
        return self._success(req_id, {"resources": resources})

    @staticmethod
    def _success(req_id: Any, result: Any) -> Dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": result,
        }

    @staticmethod
    def _error(req_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": code, "message": message},
        }

    def run_stdio(self) -> None:
        """Run the MCP server over stdio (blocking).

        Supports both Content-Length framed messages (standard MCP stdio
        protocol used by Claude Desktop, Cursor, etc.) and bare JSON-per-line
        for simple testing.
        """
        logger.info("ABLE MCP server starting on stdio")
        buf = ""
        content_length: int | None = None
        for raw_line in sys.stdin:
            line = raw_line.rstrip("\r\n")

            # Content-Length framing: header phase
            if content_length is None:
                if line.startswith("Content-Length:"):
                    try:
                        content_length = int(line.split(":", 1)[1].strip())
                    except (ValueError, IndexError):
                        pass
                    continue
                if line == "":
                    # Blank line after headers → read body
                    if content_length is not None:
                        body = sys.stdin.read(content_length)
                        content_length = None
                        self._process_message(body)
                    continue
                # No Content-Length header, try bare JSON line
                if line:
                    self._process_message(line)
            else:
                # Waiting for blank line separator after headers
                if line == "":
                    body = sys.stdin.read(content_length)
                    content_length = None
                    self._process_message(body)
                # else: additional header, skip

    def _process_message(self, data: str) -> None:
        """Parse and handle a single JSON-RPC message."""
        data = data.strip()
        if not data:
            return
        try:
            request = json.loads(data)
            response = self.handle_request(request)
            if response is not None:
                body = json.dumps(response)
                # Write with Content-Length framing
                sys.stdout.write(f"Content-Length: {len(body)}\r\n\r\n{body}")
                sys.stdout.flush()
        except json.JSONDecodeError:
            err = self._error(None, -32700, "Parse error")
            body = json.dumps(err)
            sys.stdout.write(f"Content-Length: {len(body)}\r\n\r\n{body}")
            sys.stdout.flush()
        except Exception as e:
            logger.exception("Unhandled error")
            err = self._error(None, -32603, str(e))
            body = json.dumps(err)
            sys.stdout.write(f"Content-Length: {len(body)}\r\n\r\n{body}")
            sys.stdout.flush()


# ── Entry point ────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,  # Logs to stderr, protocol on stdout
    )
    server = ABLEMCPServer()
    server.run_stdio()


if __name__ == "__main__":
    main()
