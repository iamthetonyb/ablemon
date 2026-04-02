"""
Digital Ocean API v2 client for ABLE.
Handles droplet creation, listing, and SSH key management.
"""

import asyncio
import logging
import os
from typing import Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

DO_API = "https://api.digitalocean.com/v2"


class DigitalOceanClient:
    """
    Async Digital Ocean API v2 wrapper.
    Auth via DO_API_TOKEN env var.
    """

    def __init__(self):
        self.token = os.environ.get("DO_API_TOKEN", "")

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    async def _get(self, path: str) -> Dict:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{DO_API}{path}", headers=self._headers()
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def _post(self, path: str, payload: Dict) -> Dict:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{DO_API}{path}",
                headers=self._headers(),
                json=payload,
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def _delete(self, path: str) -> None:
        async with aiohttp.ClientSession() as session:
            async with session.delete(
                f"{DO_API}{path}", headers=self._headers()
            ) as resp:
                resp.raise_for_status()

    # ── Read-only ──────────────────────────────────────────────────────────────

    async def list_droplets(self) -> List[Dict]:
        """List all droplets on the account."""
        data = await self._get("/droplets?per_page=100")
        return data.get("droplets", [])

    async def list_regions(self) -> List[Dict]:
        """List all available regions."""
        data = await self._get("/regions")
        return data.get("regions", [])

    async def list_sizes(self) -> List[Dict]:
        """List all available droplet sizes."""
        data = await self._get("/sizes")
        return data.get("sizes", [])

    async def get_droplet(self, droplet_id: int) -> Dict:
        """Get details for a specific droplet."""
        data = await self._get(f"/droplets/{droplet_id}")
        return data.get("droplet", {})

    # ── Write operations ────────────────────────────────────────────────────────

    async def create_droplet(
        self,
        name: str,
        region: str,
        size: str,
        image: str,
        ssh_key_ids: List[int],
        user_data: Optional[str] = None,
    ) -> Dict:
        """
        Create a new droplet and poll until it is active.
        Returns the full droplet dict including the public IP.
        """
        payload: Dict = {
            "name": name,
            "region": region,
            "size": size,
            "image": image,
            "ssh_keys": ssh_key_ids,
            "backups": False,
            "ipv6": False,
            "monitoring": True,
        }
        if user_data:
            payload["user_data"] = user_data

        data = await self._post("/droplets", payload)
        droplet = data.get("droplet", {})
        droplet_id = droplet.get("id")
        logger.info(f"DO: created droplet {name} (id={droplet_id}), polling until active...")

        # Poll until active (up to 5 minutes)
        for _ in range(60):
            await asyncio.sleep(5)
            droplet = await self.get_droplet(droplet_id)
            if droplet.get("status") == "active":
                logger.info(f"DO: droplet {name} is now active")
                return droplet

        logger.warning(f"DO: droplet {name} did not become active within 5 minutes")
        return droplet

    async def destroy_droplet(self, droplet_id: int) -> None:
        """Destroy a droplet by ID."""
        await self._delete(f"/droplets/{droplet_id}")
        logger.info(f"DO: destroyed droplet {droplet_id}")

    async def add_ssh_key(self, name: str, public_key: str) -> Dict:
        """Add an SSH public key to the account."""
        data = await self._post(
            "/account/keys",
            {"name": name, "public_key": public_key},
        )
        return data.get("ssh_key", {})
