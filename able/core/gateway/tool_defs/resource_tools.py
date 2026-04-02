"""Resource-plane tool definitions and handlers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from able.core.gateway.tool_registry import ToolContext, ToolRegistry


RESOURCE_LIST = {
    "type": "function",
    "function": {
        "name": "resource_list",
        "description": "List operator-visible services, models, and resource-plane inventory. Read-only.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

RESOURCE_STATUS = {
    "type": "function",
    "function": {
        "name": "resource_status",
        "description": "Get detailed status for a control-plane resource by ID. Read-only.",
        "parameters": {
            "type": "object",
            "properties": {
                "resource_id": {
                    "type": "string",
                    "description": "Resource ID such as service:able or runtime:ollama",
                }
            },
            "required": ["resource_id"],
        },
    },
}

RESOURCE_ACTION = {
    "type": "function",
    "function": {
        "name": "resource_action",
        "description": (
            "Perform an approval-gated lifecycle action on a control-plane resource. "
            "Use for explicit operator-approved actions only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "resource_id": {
                    "type": "string",
                    "description": "Resource ID such as service:able or runtime:ollama",
                },
                "action": {
                    "type": "string",
                    "description": "Lifecycle action such as restart, start, stop, status, or refresh.",
                },
                "parameters": {
                    "type": "object",
                    "description": "Optional action parameters for future resource adapters.",
                },
            },
            "required": ["resource_id", "action"],
        },
    },
}


async def handle_resource_list(args: dict, ctx: "ToolContext") -> str:
    plane = ctx.metadata["resource_plane"]
    resources = plane.list_resources()
    if not resources:
        return "No resources discovered."
    lines = [
        f"- `{resource['id']}` — {resource['name']} [{resource['status']}]"
        for resource in resources[:25]
    ]
    return "**Control-plane resources**\n" + "\n".join(lines)


async def handle_resource_status(args: dict, ctx: "ToolContext") -> str:
    plane = ctx.metadata["resource_plane"]
    resource = plane.get_resource(args["resource_id"])
    if not resource:
        return f"Unknown resource: {args['resource_id']}"
    summary = [
        f"**{resource['name']}** (`{resource['id']}`)",
        f"- Kind: {resource['kind']}",
        f"- Status: {resource['status']}",
        f"- Control mode: {resource['control_mode']}",
        f"- Allowed actions: {', '.join(resource.get('allowed_actions', [])) or 'none'}",
    ]
    if resource.get("endpoint"):
        summary.append(f"- Endpoint: {resource['endpoint']}")
    return "\n".join(summary)


async def handle_resource_action(args: dict, ctx: "ToolContext") -> str:
    plane = ctx.metadata["resource_plane"]
    approval = ctx.metadata.get("approval_result")
    approved_by = getattr(approval, "approved_by", None)
    if approved_by is None:
        return "Resource action denied: approval metadata missing."

    result = plane.perform_action(
        args["resource_id"],
        args["action"],
        parameters=args.get("parameters"),
        approved_by=str(approved_by),
        service_token_verified=True,
    )
    return json.dumps(result, indent=2, default=str)


def register_tools(registry: "ToolRegistry") -> None:
    registry.register(
        name="resource_list",
        definition=RESOURCE_LIST,
        handler=handle_resource_list,
        display_name="Resources: List",
        category="system",
        read_only=True,
        concurrent_safe=True,
        surface="control-plane",
        artifact_kind="json",
        enabled_by_default=True,
    )
    registry.register(
        name="resource_status",
        definition=RESOURCE_STATUS,
        handler=handle_resource_status,
        display_name="Resources: Status",
        category="system",
        read_only=True,
        concurrent_safe=True,
        surface="control-plane",
        artifact_kind="json",
        enabled_by_default=True,
    )
    registry.register(
        name="resource_action",
        definition=RESOURCE_ACTION,
        handler=handle_resource_action,
        display_name="Resources: Action",
        description="Execute an approval-gated lifecycle action against a registered resource.",
        requires_approval=True,
        risk_level="high",
        category="system",
        read_only=False,
        concurrent_safe=False,
        surface="control-plane",
        artifact_kind="json",
        enabled_by_default=True,
        tags=["approval", "lifecycle"],
    )
