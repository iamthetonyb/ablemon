"""Tests for D9 — ABLE MCP Server Mode.

Covers: JSON-RPC protocol, initialize handshake, tools/list, tools/call,
resources/list, tool handlers, error handling, discovery protocol.
"""

import json
import pytest

from able.tools.mcp.able_mcp_server import (
    ABLEMCPServer,
    ABLEToolHandlers,
    MCPToolDef,
    MCPToolResult,
    TOOL_DEFINITIONS,
)


@pytest.fixture
def server():
    return ABLEMCPServer()


@pytest.fixture
def handlers():
    return ABLEToolHandlers()


def _req(method, params=None, req_id=1):
    return {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": req_id}


# ── Protocol handshake ─────────────────────────────────────────

class TestInitialize:

    def test_initialize(self, server):
        resp = server.handle_request(_req("initialize"))
        assert resp["result"]["protocolVersion"] == "2024-11-05"
        assert resp["result"]["serverInfo"]["name"] == "able"

    def test_initialized_notification(self, server):
        resp = server.handle_request(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        assert resp is None  # Notification, no response
        assert server._initialized is True

    def test_ping(self, server):
        resp = server.handle_request(_req("ping"))
        assert "result" in resp


# ── Tools list ─────────────────────────────────────────────────

class TestToolsList:

    def test_list_tools(self, server):
        resp = server.handle_request(_req("tools/list"))
        tools = resp["result"]["tools"]
        assert len(tools) >= 9

    def test_all_tools_have_schema(self, server):
        resp = server.handle_request(_req("tools/list"))
        for tool in resp["result"]["tools"]:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool

    def test_discover_tool_present(self, server):
        resp = server.handle_request(_req("tools/list"))
        names = [t["name"] for t in resp["result"]["tools"]]
        assert "able_discover" in names
        assert "able_status" in names
        assert "able_message" in names


# ── Tools call ─────────────────────────────────────────────────

class TestToolsCall:

    def test_call_status(self, server):
        resp = server.handle_request(_req(
            "tools/call",
            {"name": "able_status", "arguments": {}},
        ))
        content = resp["result"]["content"]
        assert len(content) > 0
        data = json.loads(content[0]["text"])
        assert data["system"] == "ABLE"

    def test_call_discover(self, server):
        resp = server.handle_request(_req(
            "tools/call",
            {"name": "able_discover", "arguments": {}},
        ))
        content = resp["result"]["content"]
        data = json.loads(content[0]["text"])
        assert "tools" in data
        assert "routing_tiers" in data
        assert "usage_protocol" in data

    def test_call_message(self, server):
        resp = server.handle_request(_req(
            "tools/call",
            {"name": "able_message", "arguments": {"message": "hello"}},
        ))
        assert resp["result"]["isError"] is False

    def test_call_message_missing_param(self, server):
        resp = server.handle_request(_req(
            "tools/call",
            {"name": "able_message", "arguments": {}},
        ))
        assert resp["result"]["isError"] is True

    def test_call_skills(self, server):
        resp = server.handle_request(_req(
            "tools/call",
            {"name": "able_skills", "arguments": {}},
        ))
        assert resp["result"]["isError"] is False

    def test_call_memory_search(self, server):
        resp = server.handle_request(_req(
            "tools/call",
            {"name": "able_memory_search", "arguments": {"query": "test"}},
        ))
        assert resp["result"]["isError"] is False

    def test_call_memory_search_no_query(self, server):
        resp = server.handle_request(_req(
            "tools/call",
            {"name": "able_memory_search", "arguments": {}},
        ))
        assert resp["result"]["isError"] is True

    def test_call_events_poll(self, server):
        resp = server.handle_request(_req(
            "tools/call",
            {"name": "able_events_poll", "arguments": {}},
        ))
        assert resp["result"]["isError"] is False

    def test_call_permissions(self, server):
        resp = server.handle_request(_req(
            "tools/call",
            {"name": "able_permissions", "arguments": {}},
        ))
        assert resp["result"]["isError"] is False

    def test_call_config(self, server):
        resp = server.handle_request(_req(
            "tools/call",
            {"name": "able_config", "arguments": {}},
        ))
        assert resp["result"]["isError"] is False

    def test_call_tool_list(self, server):
        resp = server.handle_request(_req(
            "tools/call",
            {"name": "able_tool_list", "arguments": {}},
        ))
        assert resp["result"]["isError"] is False

    def test_unknown_tool(self, server):
        resp = server.handle_request(_req(
            "tools/call",
            {"name": "nonexistent_tool", "arguments": {}},
        ))
        assert "error" in resp
        assert resp["error"]["code"] == -32602


# ── Resources ──────────────────────────────────────────────────

class TestResources:

    def test_list_resources(self, server):
        resp = server.handle_request(_req("resources/list"))
        resources = resp["result"]["resources"]
        assert len(resources) >= 2
        uris = [r["uri"] for r in resources]
        assert "able://config/routing" in uris
        assert "able://skills/index" in uris


# ── Error handling ─────────────────────────────────────────────

class TestErrors:

    def test_unknown_method(self, server):
        resp = server.handle_request(_req("unknown/method"))
        assert "error" in resp
        assert resp["error"]["code"] == -32601

    def test_response_format(self, server):
        resp = server.handle_request(_req("tools/list"))
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 1


# ── Tool result builder ───────────────────────────────────────

class TestMCPToolResult:

    def test_text_content(self):
        r = MCPToolResult().text("hello")
        assert r.content[0]["type"] == "text"
        assert r.content[0]["text"] == "hello"
        assert r.is_error is False

    def test_error_content(self):
        r = MCPToolResult().error("something broke")
        assert r.is_error is True
        assert "something broke" in r.content[0]["text"]

    def test_chain(self):
        r = MCPToolResult().text("first").text("second")
        assert len(r.content) == 2


# ── Tool definitions ──────────────────────────────────────────

class TestToolDefinitions:

    def test_all_definitions_valid(self):
        for td in TOOL_DEFINITIONS:
            assert isinstance(td, MCPToolDef)
            assert td.name
            assert td.description
            assert "type" in td.input_schema

    def test_required_fields_present(self):
        msg_tool = next(t for t in TOOL_DEFINITIONS if t.name == "able_message")
        assert "required" in msg_tool.input_schema
        assert "message" in msg_tool.input_schema["required"]


# ── Handlers direct ────────────────────────────────────────────

class TestHandlersDirect:

    def test_status_uptime(self, handlers):
        r = handlers.handle_status({})
        data = json.loads(r.content[0]["text"])
        assert data["uptime_s"] >= 0

    def test_discover_has_protocol(self, handlers):
        r = handlers.handle_discover({})
        data = json.loads(r.content[0]["text"])
        assert "usage_protocol" in data

    def test_events_logged(self, handlers):
        handlers.handle_message({"message": "test"})
        r = handlers.handle_events_poll({"limit": 10})
        events = json.loads(r.content[0]["text"])
        assert len(events) >= 1
        assert events[0]["type"] == "message"
