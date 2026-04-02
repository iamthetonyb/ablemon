from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiohttp import web

from able.core.gateway import gateway as gateway_module


class DummyToolRegistry:
    tool_count = 2

    def get_effective_settings(self, overrides=None):
        return {
            "resource_list": {
                "enabled": True,
                "requires_approval": False,
                "risk_level": "low",
            },
            "resource_action": {
                "enabled": True,
                "requires_approval": True,
                "risk_level": "high",
            },
        }

    def get_catalog(self):
        return [
            {
                "name": "resource_list",
                "display_name": "Resources: List",
                "description": "List resources",
                "category": "system",
                "read_only": True,
                "concurrent_safe": True,
                "surface": "control-plane",
                "artifact_kind": "json",
                "enabled_by_default": True,
                "tags": [],
                "parameters": {"type": "object"},
            },
            {
                "name": "resource_action",
                "display_name": "Resources: Action",
                "description": "Act on resource",
                "category": "system",
                "read_only": False,
                "concurrent_safe": False,
                "surface": "control-plane",
                "artifact_kind": "json",
                "enabled_by_default": True,
                "tags": ["approval"],
                "parameters": {"type": "object"},
            },
        ]

    def get_definitions(self, effective=None):
        return [
            {
                "type": "function",
                "function": {"name": "resource_list", "parameters": {"type": "object"}},
            },
            {
                "type": "function",
                "function": {"name": "resource_action", "parameters": {"type": "object"}},
            },
        ]


class DummyResourcePlane:
    def __init__(self):
        self.calls = []

    def list_resources(self):
        return [
            {
                "id": "service:able",
                "name": "ABLE",
                "status": "running",
                "kind": "service",
                "control_mode": "managed",
                "allowed_actions": ["restart", "status"],
            }
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
            "log_artifact": {"kind": "text", "content": "ok"},
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
        if not approved_by:
            return {"status": "approval_required", "resource_id": resource_id, "action": action}
        if not service_token_verified:
            return {"status": "unauthorized", "resource_id": resource_id, "action": action}
        return {
            "status": "completed",
            "resource_id": resource_id,
            "action": action,
            "parameters": parameters or {},
        }

    def list_collections(self):
        return [{"id": "distillation-box", "name": "Distillation Box"}]

    def get_setup_wizard(self):
        return {"title": "ABLE Setup Wizard", "steps": [{"id": "gateway", "status": "running"}]}


@pytest.fixture
def control_gateway(monkeypatch):
    async def fake_fetch_tool_settings(org_id=None):
        return {}

    monkeypatch.setattr(gateway_module, "ABLE_SERVICE_TOKEN", "test-token")
    monkeypatch.setattr(gateway_module, "fetch_tool_settings", fake_fetch_tool_settings)

    gateway = gateway_module.ABLEGateway.__new__(gateway_module.ABLEGateway)
    gateway.client_bots = {}
    gateway.master_bot = None
    gateway.provider_chain = SimpleNamespace(providers=["tier1", "tier2"])
    gateway.tool_registry = DummyToolRegistry()
    gateway.resource_plane = DummyResourcePlane()
    return gateway


@pytest.fixture
def control_app(control_gateway):
    app = web.Application()
    app.router.add_get("/health", control_gateway._health_handler)
    app.router.add_get("/control/tools/catalog", control_gateway._control_tools_catalog_handler)
    app.router.add_get("/control/resources", control_gateway._control_resources_handler)
    app.router.add_get("/control/resources/{resource_id}", control_gateway._control_resource_detail_handler)
    app.router.add_post("/control/resources/{resource_id}/action", control_gateway._control_resource_action_handler)
    app.router.add_get("/control/collections", control_gateway._control_collections_handler)
    app.router.add_get("/control/setup-wizard", control_gateway._control_setup_wizard_handler)
    return app


@pytest.mark.asyncio
async def test_health_returns_status(aiohttp_client, control_app):
    client = await aiohttp_client(control_app)
    resp = await client.get("/health")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "ok"
    assert data["control_plane"] == "enabled"


@pytest.mark.asyncio
async def test_tools_catalog_requires_service_token(aiohttp_client, control_app):
    client = await aiohttp_client(control_app)
    resp = await client.get("/control/tools/catalog")
    assert resp.status == 401
    assert await resp.json() == {"error": "unauthorized"}


@pytest.mark.asyncio
async def test_tools_catalog_returns_catalog_with_token(aiohttp_client, control_app):
    client = await aiohttp_client(control_app)
    resp = await client.get(
        "/control/tools/catalog?org_id=acme",
        headers={"x-able-service-token": "test-token"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["organization_id"] == "acme"
    assert len(data["catalog"]) == 2
    assert len(data["definitions"]) == 2


@pytest.mark.asyncio
async def test_resources_returns_inventory(aiohttp_client, control_app):
    client = await aiohttp_client(control_app)
    resp = await client.get(
        "/control/resources",
        headers={"x-able-service-token": "test-token"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["resources"][0]["id"] == "service:able"


@pytest.mark.asyncio
async def test_resource_detail_known_and_unknown(aiohttp_client, control_app):
    client = await aiohttp_client(control_app)

    known = await client.get(
        "/control/resources/service:able",
        headers={"x-able-service-token": "test-token"},
    )
    assert known.status == 200
    known_data = await known.json()
    assert known_data["id"] == "service:able"

    missing = await client.get(
        "/control/resources/unknown:thing",
        headers={"x-able-service-token": "test-token"},
    )
    assert missing.status == 404
    assert await missing.json() == {"error": "resource_not_found"}


@pytest.mark.asyncio
async def test_resource_action_requires_token_and_approval_metadata(
    aiohttp_client,
    control_app,
    control_gateway,
):
    client = await aiohttp_client(control_app)

    unauthorized = await client.post(
        "/control/resources/service:able/action",
        json={"action": "restart"},
    )
    assert unauthorized.status == 401

    awaiting_approval = await client.post(
        "/control/resources/service:able/action",
        json={"action": "restart"},
        headers={"x-able-service-token": "test-token"},
    )
    assert awaiting_approval.status == 202
    data = await awaiting_approval.json()
    assert data["status"] == "approval_required"
    assert control_gateway.resource_plane.calls[-1]["service_token_verified"] is True


@pytest.mark.asyncio
async def test_resource_action_executes_with_token_and_approved_by(
    aiohttp_client,
    control_app,
    control_gateway,
):
    client = await aiohttp_client(control_app)

    resp = await client.post(
        "/control/resources/service:able/action",
        json={
            "action": "restart",
            "approved_by": "operator",
            "parameters": {"reason": "test"},
        },
        headers={"x-able-service-token": "test-token"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "completed"
    assert control_gateway.resource_plane.calls[-1]["approved_by"] == "operator"
    assert control_gateway.resource_plane.calls[-1]["parameters"] == {"reason": "test"}


@pytest.mark.asyncio
async def test_collections_and_setup_wizard_return_payloads(aiohttp_client, control_app):
    client = await aiohttp_client(control_app)
    headers = {"x-able-service-token": "test-token"}

    collections = await client.get("/control/collections", headers=headers)
    assert collections.status == 200
    assert (await collections.json())["collections"][0]["id"] == "distillation-box"

    setup = await client.get("/control/setup-wizard", headers=headers)
    assert setup.status == 200
    assert (await setup.json())["steps"][0]["id"] == "gateway"
