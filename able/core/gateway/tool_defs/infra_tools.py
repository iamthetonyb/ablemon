"""
Infrastructure tool definitions and handlers (Digital Ocean + Vercel).
Extracted from gateway.py for modularity.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.gateway.tool_registry import ToolRegistry, ToolContext

logger = logging.getLogger(__name__)


# ── Tool Definitions ──────────────────────────────────────────────────────────

DO_LIST_DROPLETS = {
    "type": "function",
    "function": {
        "name": "do_list_droplets",
        "description": "List all Digital Ocean droplets. Read-only, no approval needed.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

DO_CREATE_DROPLET = {
    "type": "function",
    "function": {
        "name": "do_create_droplet",
        "description": "Provision a new Digital Ocean VPS droplet. Billable ($6+/month). Requires owner approval.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Droplet name in kebab-case"},
                "region": {"type": "string", "description": "Region slug (e.g. nyc3, sfo3, ams3). Default: nyc3"},
                "size": {"type": "string", "description": "Size slug (e.g. s-1vcpu-1gb, s-2vcpu-2gb). Default: s-1vcpu-1gb"},
                "image": {"type": "string", "description": "Image slug (e.g. ubuntu-24-04-x64). Default: ubuntu-24-04-x64"},
                "ssh_key_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "List of SSH key IDs from DO account",
                },
            },
            "required": ["name"],
        },
    },
}

VERCEL_DEPLOY = {
    "type": "function",
    "function": {
        "name": "vercel_deploy",
        "description": "Deploy a frontend or serverless app to Vercel. Free tier. Requires owner approval.",
        "parameters": {
            "type": "object",
            "properties": {
                "project_name": {"type": "string", "description": "Vercel project name in kebab-case"},
                "files": {
                    "type": "object",
                    "description": "Map of {filepath: file_content_string}",
                    "additionalProperties": {"type": "string"},
                },
                "env_vars": {
                    "type": "object",
                    "description": "Optional environment variables",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["project_name", "files"],
        },
    },
}


# ── Handlers ──────────────────────────────────────────────────────────────────

async def handle_list_droplets(args: dict, ctx: "ToolContext") -> str:
    do_client = ctx.metadata["do_client"]
    droplets = await do_client.list_droplets()
    if not droplets:
        return "No droplets found on this account."
    lines = []
    for d in droplets:
        networks = d.get("networks", {}).get("v4", [])
        ip = next((n["ip_address"] for n in networks if n.get("type") == "public"), "no-ip")
        lines.append(f"• **{d['name']}** — {ip} ({d['region']['slug']}, {d['size_slug']}, {d['status']})")
    return "**Droplets:**\n" + "\n".join(lines)


async def handle_create_droplet(args: dict, ctx: "ToolContext") -> str:
    do_client = ctx.metadata["do_client"]
    d_name = args["name"]
    region = args.get("region", "nyc3")
    size = args.get("size", "s-1vcpu-1gb")
    image = args.get("image", "ubuntu-24-04-x64")
    ssh_key_ids = args.get("ssh_key_ids", [])
    size_costs = {"s-1vcpu-1gb": "$6/mo", "s-2vcpu-2gb": "$12/mo", "s-4vcpu-8gb": "$48/mo"}
    cost = size_costs.get(size, "variable")

    droplet = await do_client.create_droplet(
        name=d_name, region=region, size=size, image=image, ssh_key_ids=ssh_key_ids,
    )
    networks = droplet.get("networks", {}).get("v4", [])
    public_ip = next((n["ip_address"] for n in networks if n.get("type") == "public"), "pending")
    status = droplet.get("status", "unknown")

    if status == "active":
        return (
            f"✅ Droplet ready!\n\n"
            f"**{d_name}**\n"
            f"IP: `{public_ip}`\n"
            f"Region: {region} | {size} | {cost}\n\n"
            f"SSH: `ssh root@{public_ip}`"
        )
    return f"⏳ Droplet created (status: {status})\nID: {droplet.get('id')}"


async def handle_vercel_deploy(args: dict, ctx: "ToolContext") -> str:
    vercel = ctx.metadata["vercel"]
    project_name = args["project_name"]
    files_dict = args["files"]
    env_vars = args.get("env_vars")

    result = await vercel.create_deployment(project_name, files_dict, env_vars)
    state = result.get("readyState", "UNKNOWN")
    url = result.get("url", "")
    if state == "READY":
        live_url = f"https://{url}" if url and not url.startswith("http") else url
        return f"✅ Deployed to Vercel!\n\n🌐 {live_url}\n📦 {project_name}"
    elif state == "ERROR":
        return f"❌ Vercel failed: {result.get('errorMessage', 'Unknown error')}"
    else:
        return f"⏳ Deploying... state: {state}\nCheck vercel.com/dashboard"


# ── Registration ──────────────────────────────────────────────────────────────

def register_tools(registry: "ToolRegistry"):
    """Register all infrastructure tools with the registry."""
    registry.register(
        name="do_list_droplets",
        definition=DO_LIST_DROPLETS,
        handler=handle_list_droplets,
        display_name="DigitalOcean / List Droplets",
        requires_approval=False,
        category="system",
        read_only=True,
        concurrent_safe=True,
        surface="digitalocean",
        artifact_kind="markdown",
        tags=["infra", "read"],
    )
    registry.register(
        name="do_create_droplet",
        definition=DO_CREATE_DROPLET,
        handler=handle_create_droplet,
        display_name="DigitalOcean / Create Droplet",
        requires_approval=True,
        risk_level="high",
        category="execution",
        read_only=False,
        concurrent_safe=False,
        surface="digitalocean",
        artifact_kind="markdown",
        tags=["infra", "billable"],
    )
    registry.register(
        name="vercel_deploy",
        definition=VERCEL_DEPLOY,
        handler=handle_vercel_deploy,
        display_name="Vercel / Deploy",
        requires_approval=True,
        risk_level="low",
        category="execution",
        read_only=False,
        concurrent_safe=False,
        surface="vercel",
        artifact_kind="markdown",
        tags=["deploy", "frontend"],
    )
