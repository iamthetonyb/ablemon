"""
Digital Ocean VPS Skill — implement.py

Provisions a new DO droplet, polls until active, returns SSH access info.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default values matching SKILL.md protocol
DEFAULT_IMAGE = "ubuntu-24-04-x64"
DEFAULT_REGION = "nyc3"
DEFAULT_SIZE = "s-1vcpu-1gb"
SIZE_COSTS = {
    "s-1vcpu-1gb": "$6/mo",
    "s-2vcpu-2gb": "$12/mo",
    "s-4vcpu-8gb": "$48/mo",
    "c-4": "$84/mo",
}


async def run(
    params: Dict[str, Any],
    do_client,
    approval_workflow,
    requester_id: str = "owner",
) -> str:
    """
    Provision a new Digital Ocean droplet.

    Args:
        params: {
            "name": str,                   # Droplet name
            "region": str,                 # Region slug (default: nyc3)
            "size": str,                   # Size slug (default: s-1vcpu-1gb)
            "image": str,                  # Image slug (default: ubuntu-24-04-x64)
            "ssh_key_ids": list[int],      # List of SSH key IDs from DO account
            "user_data": str,              # Optional cloud-init script
        }
        do_client: DigitalOceanClient instance
        approval_workflow: ApprovalWorkflow instance
        requester_id: Telegram user ID string

    Returns:
        Human-readable result with IP and SSH access info
    """
    name = params["name"]
    region = params.get("region", DEFAULT_REGION)
    size = params.get("size", DEFAULT_SIZE)
    image = params.get("image", DEFAULT_IMAGE)
    ssh_key_ids: List[int] = params.get("ssh_key_ids", [])
    user_data: Optional[str] = params.get("user_data")

    cost = SIZE_COSTS.get(size, "variable")

    # Request approval — HIGH RISK (billable infrastructure)
    approval = await approval_workflow.request_approval(
        operation="do_create_droplet",
        details={
            "name": name,
            "region": region,
            "size": size,
            "image": image,
            "estimated_cost": cost,
        },
        requester_id=requester_id,
        risk_level="high",
        context=(
            f"Provision new DO droplet\n"
            f"Name: {name}\n"
            f"Region: {region} | Size: {size} | Image: {image}\n"
            f"Cost: {cost} (billed immediately)"
        ),
    )
    if approval.status.value != "approved":
        return f"❌ VPS provisioning denied ({approval.status.value})"

    # Create droplet (polls internally until active)
    droplet = await do_client.create_droplet(
        name=name,
        region=region,
        size=size,
        image=image,
        ssh_key_ids=ssh_key_ids,
        user_data=user_data,
    )

    # Extract public IP
    networks = droplet.get("networks", {})
    v4 = networks.get("v4", [])
    public_ip = next(
        (n["ip_address"] for n in v4 if n.get("type") == "public"),
        "pending"
    )

    droplet_id = droplet.get("id", "unknown")
    status = droplet.get("status", "unknown")

    if status == "active":
        return (
            f"✅ Droplet ready!\n\n"
            f"**{name}**\n"
            f"IP: `{public_ip}`\n"
            f"Region: {region} | Size: {size}\n"
            f"Image: {image}\n"
            f"Cost: {cost}\n\n"
            f"SSH: `ssh root@{public_ip}`\n"
            f"ID: {droplet_id}"
        )
    else:
        return (
            f"⏳ Droplet created (status: {status})\n"
            f"Name: {name} | ID: {droplet_id}\n"
            f"IP may not be assigned yet. Check DO dashboard."
        )
