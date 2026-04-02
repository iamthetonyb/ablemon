"""
Vercel REST API client for ABLE.
Handles project listing and deployments.
"""

import asyncio
import logging
import os
from typing import Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

VERCEL_API = "https://api.vercel.com"


class VercelClient:
    """
    Async Vercel REST API wrapper.
    Auth via VERCEL_TOKEN env var.
    """

    def __init__(self):
        self.token = os.environ.get("VERCEL_TOKEN", "")

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    async def _get(self, path: str) -> Dict:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{VERCEL_API}{path}", headers=self._headers()
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def _post(self, path: str, payload: Dict) -> Dict:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{VERCEL_API}{path}",
                headers=self._headers(),
                json=payload,
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

    # ── Read-only ──────────────────────────────────────────────────────────────

    async def list_projects(self) -> List[Dict]:
        """List all Vercel projects."""
        data = await self._get("/v9/projects?limit=100")
        return data.get("projects", [])

    async def get_deployment(self, deployment_id: str) -> Dict:
        """Get details for a specific deployment."""
        return await self._get(f"/v13/deployments/{deployment_id}")

    # ── Write operations ────────────────────────────────────────────────────────

    async def create_deployment(
        self,
        project_name: str,
        files_dict: Dict[str, str],
        env_vars: Optional[Dict[str, str]] = None,
    ) -> Dict:
        """
        Deploy files to Vercel and poll until the deployment is ready.

        Args:
            project_name: Vercel project name (created automatically if new).
            files_dict: {filename: file_content_string} for the deployment.
            env_vars: Optional environment variables to set.

        Returns:
            Deployment dict with 'url' key for the live URL.
        """
        # Build file list in Vercel format
        vercel_files = [
            {"file": path, "data": content}
            for path, content in files_dict.items()
        ]

        payload: Dict = {
            "name": project_name,
            "files": vercel_files,
            "projectSettings": {"framework": None},
        }
        if env_vars:
            payload["env"] = [
                {"key": k, "value": v, "type": "plain"}
                for k, v in env_vars.items()
            ]

        data = await self._post("/v13/deployments", payload)
        deployment_id = data.get("id")
        logger.info(f"Vercel: created deployment {deployment_id} for {project_name}, polling...")

        # Poll until ready (up to 3 minutes)
        for _ in range(36):
            await asyncio.sleep(5)
            status = await self.get_deployment(deployment_id)
            state = status.get("readyState", "")
            if state in ("READY", "ERROR", "CANCELED"):
                logger.info(f"Vercel: deployment {deployment_id} reached state {state}")
                return status

        logger.warning(f"Vercel: deployment {deployment_id} did not finish within 3 minutes")
        return data
