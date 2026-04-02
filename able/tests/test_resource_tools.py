from __future__ import annotations

import asyncio
from types import SimpleNamespace

from able.core.approval.workflow import ApprovalResult, ApprovalStatus
from able.core.gateway.tool_defs import resource_tools
from able.core.gateway.tool_registry import ToolContext, ToolRegistry


class DummyPlane:
    def __init__(self):
        self.calls = []

    def list_resources(self):
        return [
            {"id": "service:able", "name": "ABLE", "status": "running"},
            {"id": "runtime:ollama", "name": "Ollama", "status": "ready"},
        ]

    def get_resource(self, resource_id: str):
        if resource_id != "service:able":
            return None
        return {
            "id": "service:able",
            "name": "ABLE",
            "status": "running",
            "kind": "service",
            "control_mode": "managed",
            "allowed_actions": ["restart", "status"],
        }

    def perform_action(
        self,
        resource_id: str,
        action: str,
        *,
        parameters=None,
        approved_by=None,
        service_token_verified=False,
    ):
        self.calls.append(
            {
                "resource_id": resource_id,
                "action": action,
                "parameters": parameters,
                "approved_by": approved_by,
                "service_token_verified": service_token_verified,
            }
        )
        return {"status": "completed", "resource_id": resource_id, "action": action}


class ApprovingWorkflow:
    async def request_approval(self, **kwargs):
        return ApprovalResult(
            request_id="req-1",
            status=ApprovalStatus.APPROVED,
            approved_by=42,
        )


class DenyingWorkflow:
    async def request_approval(self, **kwargs):
        return ApprovalResult(
            request_id="req-2",
            status=ApprovalStatus.DENIED,
            approved_by=42,
        )


def _build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    resource_tools.register_tools(registry)
    return registry


def _tool_context(plane, workflow) -> ToolContext:
    return ToolContext(
        user_id="cli-user",
        client_id="master",
        approval_workflow=workflow,
        metadata={"resource_plane": plane},
    )


def test_resource_list_formats_inventory():
    plane = DummyPlane()
    result = asyncio.run(resource_tools.handle_resource_list({}, _tool_context(plane, None)))
    assert "**Control-plane resources**" in result
    assert "`service:able`" in result


def test_resource_status_reports_known_and_unknown_resources():
    plane = DummyPlane()
    known = asyncio.run(
        resource_tools.handle_resource_status({"resource_id": "service:able"}, _tool_context(plane, None))
    )
    assert "**ABLE** (`service:able`)" in known

    unknown = asyncio.run(
        resource_tools.handle_resource_status({"resource_id": "unknown:thing"}, _tool_context(plane, None))
    )
    assert unknown == "Unknown resource: unknown:thing"


def test_resource_action_dispatches_after_approval():
    plane = DummyPlane()
    registry = _build_registry()
    tool_call = SimpleNamespace(
        name="resource_action",
        arguments={
            "resource_id": "service:able",
            "action": "restart",
            "parameters": {"reason": "test"},
        },
    )

    result = asyncio.run(
        registry.dispatch(tool_call, _tool_context(plane, ApprovingWorkflow()))
    )

    assert '"status": "completed"' in result
    assert plane.calls[0]["approved_by"] == "42"
    assert plane.calls[0]["service_token_verified"] is True


def test_resource_action_stops_when_approval_denied():
    plane = DummyPlane()
    registry = _build_registry()
    tool_call = SimpleNamespace(
        name="resource_action",
        arguments={"resource_id": "service:able", "action": "restart"},
    )

    result = asyncio.run(
        registry.dispatch(tool_call, _tool_context(plane, DenyingWorkflow()))
    )

    assert result == "❌ Denied (denied)"
    assert plane.calls == []
