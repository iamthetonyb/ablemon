"""
GitHub REST API v3 client for ATLAS.
Handles repo creation, branch management, file pushes, PRs, and Pages.
"""

import base64
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


class GitHubClient:
    """
    Async GitHub REST API v3 wrapper.

    Auth via GITHUB_TOKEN env var.
    Owner via ATLAS_OWNER_USERNAME env var.
    All writes are appended to audit/logs/github_actions.jsonl.
    """

    def __init__(self):
        self.token = os.environ.get("GITHUB_TOKEN", "")
        self.owner = os.environ.get("ATLAS_OWNER_USERNAME", "").lstrip("@")
        self.audit_log = Path("audit/logs/github_actions.jsonl")
        self.audit_log.parent.mkdir(parents=True, exist_ok=True)

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
        }

    def _log(self, action: str, data: Dict):
        """Append write action to audit log."""
        entry = {
            "ts": datetime.utcnow().isoformat(),
            "action": action,
            **data,
        }
        with open(self.audit_log, "a") as f:
            f.write(json.dumps(entry) + "\n")

    async def _get(self, path: str) -> Dict:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{GITHUB_API}{path}", headers=self._headers()
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def _post(self, path: str, payload: Dict) -> Dict:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{GITHUB_API}{path}",
                headers=self._headers(),
                json=payload,
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

    async def _put(self, path: str, payload: Dict) -> Dict:
        async with aiohttp.ClientSession() as session:
            async with session.put(
                f"{GITHUB_API}{path}",
                headers=self._headers(),
                json=payload,
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

    # ── Read-only ──────────────────────────────────────────────────────────────

    async def list_repos(self) -> List[Dict]:
        """List all repos for the authenticated user."""
        return await self._get("/user/repos?per_page=100&sort=updated")

    # ── Write operations ────────────────────────────────────────────────────────

    async def create_repo(
        self,
        name: str,
        description: str = "",
        private: bool = False,
    ) -> Dict:
        """Create a new GitHub repository."""
        result = await self._post(
            "/user/repos",
            {"name": name, "description": description, "private": private},
        )
        self._log("create_repo", {"repo": name, "private": private, "url": result.get("html_url")})
        return result

    async def create_branch(
        self,
        repo: str,
        branch: str,
        from_branch: str = "main",
    ) -> Dict:
        """Create a new branch from an existing branch."""
        if repo.startswith(f"{self.owner}/"):
            repo = repo[len(self.owner) + 1:]
        # Get SHA of source branch
        ref_data = await self._get(f"/repos/{self.owner}/{repo}/git/refs/heads/{from_branch}")
        sha = ref_data["object"]["sha"]
        result = await self._post(
            f"/repos/{self.owner}/{repo}/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": sha},
        )
        self._log("create_branch", {"repo": repo, "branch": branch, "from": from_branch})
        return result

    async def push_file(
        self,
        repo: str,
        path: str,
        content: str,
        message: str,
        branch: str = "main",
    ) -> Dict:
        """
        Create or update a single file in a repo.
        content is raw string; it will be base64-encoded automatically.
        """
        # Ensure repo doesn't contain owner/ prefix (AI sometimes provides "owner/repo")
        if repo.startswith(f"{self.owner}/"):
            repo = repo[len(self.owner) + 1:]
        encoded = base64.b64encode(content.encode()).decode()
        payload: Dict = {
            "message": message,
            "content": encoded,
            "branch": branch,
        }

        # If file already exists, we need its SHA to update it
        try:
            existing = await self._get(f"/repos/{self.owner}/{repo}/contents/{path}?ref={branch}")
            payload["sha"] = existing["sha"]
        except aiohttp.ClientResponseError as e:
            if e.status != 404:
                raise

        result = await self._put(
            f"/repos/{self.owner}/{repo}/contents/{path}",
            payload,
        )
        self._log("push_file", {"repo": repo, "path": path, "branch": branch, "message": message})
        return result

    async def push_files(
        self,
        repo: str,
        files_dict: Dict[str, str],
        message: str,
        branch: str = "main",
    ) -> List[Dict]:
        """
        Push multiple files. files_dict is {path: content}.
        Each file is pushed individually via PUT /contents/{path}.
        """
        results = []
        for path, content in files_dict.items():
            result = await self.push_file(repo, path, content, message, branch)
            results.append(result)
        return results

    async def create_pr(
        self,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str = "main",
    ) -> Dict:
        """Open a pull request."""
        if repo.startswith(f"{self.owner}/"):
            repo = repo[len(self.owner) + 1:]
        result = await self._post(
            f"/repos/{self.owner}/{repo}/pulls",
            {"title": title, "body": body, "head": head, "base": base},
        )
        self._log("create_pr", {"repo": repo, "title": title, "head": head, "base": base, "url": result.get("html_url")})
        return result

    async def enable_github_pages(
        self,
        repo: str,
        branch: str = "gh-pages",
    ) -> Dict:
        """Enable GitHub Pages for a repo from the given branch."""
        if repo.startswith(f"{self.owner}/"):
            repo = repo[len(self.owner) + 1:]
        result = await self._post(
            f"/repos/{self.owner}/{repo}/pages",
            {"source": {"branch": branch, "path": "/"}},
        )
        self._log("enable_github_pages", {"repo": repo, "branch": branch})
        return result
