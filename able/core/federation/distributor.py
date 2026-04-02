"""Distribution backends for federated corpus sharing.

Uses a pluggable DistributionBackend protocol (inspired by vLLM Ascend's
hardware-pluggable interface). GitHub Releases is the first backend;
future backends (HTTP, S3, IPFS) slot in without changing sync.py.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Protocol, runtime_checkable

from able.core.federation.models import ContributionPackage

logger = logging.getLogger(__name__)

_DEFAULT_INBOX = Path.home() / ".able" / "federation" / "inbox"
_DEFAULT_OUTBOX = Path.home() / ".able" / "federation" / "outbox"
_DEFAULT_PROCESSED = Path.home() / ".able" / "federation" / "processed"

# Default network corpus repo (configurable in instance.yaml)
DEFAULT_NETWORK_REPO = "able-network-corpus"


@runtime_checkable
class DistributionBackend(Protocol):
    """Protocol for corpus distribution backends."""

    async def publish(self, package: ContributionPackage) -> Optional[str]:
        """Publish a contribution. Returns URL/ID or None on failure."""
        ...

    async def fetch_contributions(
        self, since: Optional[str] = None
    ) -> List[Path]:
        """Fetch new contributions. Returns list of downloaded file paths."""
        ...


class GitHubReleasesBackend:
    """Distribute corpus contributions via GitHub Releases.

    Each contribution becomes a release asset tagged with date + instance ID.
    GitHub Releases provide: 2GB per asset, built-in CDN, offline resilience.
    """

    def __init__(self, repo: str = DEFAULT_NETWORK_REPO):
        self.repo = repo

    async def publish(self, package: ContributionPackage) -> Optional[str]:
        """Upload contribution as a GitHub Release asset."""
        try:
            from able.tools.github.client import GitHubClient

            client = GitHubClient()
            if not client.token:
                logger.warning("Federation: no GITHUB_TOKEN — queuing to outbox")
                return None

            tag = (
                f"corpus-{package.created_at.strftime('%Y%m%d-%H%M%S')}"
                f"-{package.instance_id[:8]}"
            )
            domain_summary = ", ".join(
                f"{d}: {c}" for d, c in sorted(package.domains.items())
            )

            release = await client.create_release(
                repo=self.repo,
                tag=tag,
                name=f"Corpus contribution ({package.pair_count} pairs)",
                body=(
                    f"Instance: {package.instance_id[:8]}\n"
                    f"Pairs: {package.pair_count}\n"
                    f"Domains: {domain_summary}\n"
                    f"Created: {package.created_at.isoformat()}"
                ),
            )

            upload_url = release.get("upload_url", "")
            if upload_url:
                await client.upload_release_asset(upload_url, package.path)

            url = release.get("html_url", "")
            logger.info("Federation: published %d pairs as %s", package.pair_count, tag)
            return url

        except Exception as e:
            logger.warning("Federation: publish failed (%s) — contribution stays in outbox", e)
            return None

    async def fetch_contributions(
        self, since: Optional[str] = None
    ) -> List[Path]:
        """Download new contribution assets from GitHub Releases."""
        inbox = _DEFAULT_INBOX
        inbox.mkdir(parents=True, exist_ok=True)

        try:
            from able.tools.github.client import GitHubClient

            client = GitHubClient()
            if not client.token:
                logger.debug("Federation: no GITHUB_TOKEN — skipping fetch")
                return []

            releases = await client.list_releases(self.repo, per_page=50)
            if not isinstance(releases, list):
                return []

            downloaded: List[Path] = []
            for release in releases:
                tag = release.get("tag_name", "")

                # Skip older releases if we have a since marker
                if since and tag <= since:
                    continue

                for asset in release.get("assets", []):
                    name = asset.get("name", "")
                    if not name.endswith(".jsonl"):
                        continue

                    dest = inbox / name
                    if dest.exists():
                        continue  # Already downloaded

                    asset_url = asset.get("url", "")
                    if not asset_url:
                        continue

                    try:
                        await client.download_release_asset(asset_url, dest)
                        downloaded.append(dest)
                    except Exception as dl_err:
                        logger.warning("Federation: download failed for %s: %s", name, dl_err)

            if downloaded:
                logger.info("Federation: fetched %d new contribution files", len(downloaded))
            return downloaded

        except Exception as e:
            logger.warning("Federation: fetch failed (%s) — will retry next sync", e)
            return []


async def drain_outbox(backend: DistributionBackend) -> int:
    """Attempt to publish any queued contributions from the outbox."""
    outbox = _DEFAULT_OUTBOX
    if not outbox.exists():
        return 0

    published = 0
    for jsonl in sorted(outbox.glob("*.jsonl")):
        # Parse minimal metadata from the file to create a package
        try:
            import json

            with open(jsonl) as f:
                first_line = f.readline().strip()
            meta = json.loads(first_line) if first_line else {}

            package = ContributionPackage(
                path=jsonl,
                pair_count=meta.get("pair_count", 0),
                domains=meta.get("domains", {}),
                instance_id=meta.get("instance_id", "unknown"),
                created_at=datetime.now(timezone.utc),
            )

            url = await backend.publish(package)
            if url:
                # Move to processed
                processed = _DEFAULT_PROCESSED
                processed.mkdir(parents=True, exist_ok=True)
                shutil.move(str(jsonl), str(processed / jsonl.name))
                published += 1
        except Exception as e:
            logger.debug("Federation: outbox drain failed for %s: %s", jsonl.name, e)

    if published:
        logger.info("Federation: drained %d queued contributions from outbox", published)
    return published
