"""
Dataset Versioner — git-style versioning for training datasets.

Follows the atlas/audit/git_trail.py pattern of structured, auditable
version management. Each version is a directory with metadata.yaml
and a 'latest' symlink for quick access.

Layout:
  {corpus_dir}/{tenant_id}/
    v001/
      train.jsonl
      val.jsonl
      test.jsonl
      metadata.yaml
    v002/
      ...
    latest -> v002
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class DatasetVersion:
    """Metadata for a single dataset version."""

    version: str
    created_at: datetime
    pair_count: int
    domains: dict[str, int]
    avg_quality: float
    tenant_id: str
    notes: str = ""


class DatasetVersioner:
    """Git-based versioning for training datasets.

    Follows the atlas/audit/git_trail.py pattern: every version is
    an immutable snapshot with metadata, and a 'latest' symlink
    provides stable access to the current version.
    """

    def __init__(self, corpus_dir: str | None = None):
        self.corpus_dir = Path(
            corpus_dir or os.path.expanduser("~/.atlas/distillation/corpus")
        )

    def create_version(
        self, version: str, metadata: dict, tenant_id: str = "default"
    ) -> DatasetVersion:
        """Create a new version with metadata.yaml.

        Args:
            version: Version string (e.g. "v001")
            metadata: Dict with version metadata (written to metadata.yaml)
            tenant_id: Tenant identifier for isolation

        Returns:
            DatasetVersion record
        """
        version_dir = self.corpus_dir / tenant_id / version
        version_dir.mkdir(parents=True, exist_ok=True)

        # Write metadata
        meta_path = version_dir / "metadata.yaml"
        meta_to_write = {
            **metadata,
            "version": version,
            "tenant_id": tenant_id,
            "created_at": metadata.get(
                "created_at", datetime.now(timezone.utc).isoformat()
            ),
        }

        try:
            import yaml

            meta_path.write_text(
                yaml.dump(meta_to_write, default_flow_style=False, sort_keys=False)
            )
        except ImportError:
            meta_path = version_dir / "metadata.json"
            meta_path.write_text(json.dumps(meta_to_write, indent=2))

        self.update_symlink(version, tenant_id)

        created_at = self._parse_datetime(meta_to_write["created_at"])

        logger.info("Created dataset version %s/%s", tenant_id, version)

        return DatasetVersion(
            version=version,
            created_at=created_at,
            pair_count=metadata.get("total", metadata.get("pair_count", 0)),
            domains=metadata.get("domains", {}),
            avg_quality=metadata.get("avg_quality", 0.0),
            tenant_id=tenant_id,
            notes=metadata.get("notes", ""),
        )

    def list_versions(self, tenant_id: str = "default") -> list[DatasetVersion]:
        """List all versions for a tenant, sorted by creation date."""
        tenant_dir = self.corpus_dir / tenant_id
        if not tenant_dir.exists():
            return []

        versions = []
        for d in sorted(tenant_dir.iterdir()):
            if not d.is_dir() or not d.name.startswith("v") or d.is_symlink():
                continue
            meta = self._read_metadata(d)
            if meta is None:
                continue
            versions.append(self._meta_to_version(meta, d.name, tenant_id))

        versions.sort(key=lambda v: v.created_at)
        return versions

    def get_latest(self, tenant_id: str = "default") -> DatasetVersion | None:
        """Get the latest version."""
        versions = self.list_versions(tenant_id)
        return versions[-1] if versions else None

    def update_symlink(self, version: str, tenant_id: str = "default") -> None:
        """Update 'latest' symlink to point to this version."""
        tenant_dir = self.corpus_dir / tenant_id
        link_path = tenant_dir / "latest"
        target = tenant_dir / version

        if not target.exists():
            logger.warning("Version dir %s does not exist, skipping symlink", target)
            return

        # Remove existing symlink/file
        if link_path.is_symlink() or link_path.exists():
            link_path.unlink()

        try:
            link_path.symlink_to(target.resolve())
            logger.debug("Updated latest symlink: %s -> %s", link_path, target)
        except OSError as e:
            logger.warning("Could not create symlink (OS limitation): %s", e)

    def diff_versions(
        self, v1: str, v2: str, tenant_id: str = "default"
    ) -> dict:
        """Compare two versions: pair count delta, quality delta, domain changes.

        Returns:
            Dict with keys: v1, v2, pair_count_delta, quality_delta,
            domains_added, domains_removed, domains_changed
        """
        meta1 = self._read_metadata(self.corpus_dir / tenant_id / v1)
        meta2 = self._read_metadata(self.corpus_dir / tenant_id / v2)

        if meta1 is None or meta2 is None:
            missing = v1 if meta1 is None else v2
            return {"error": f"Version {missing} not found for tenant {tenant_id}"}

        count1 = meta1.get("total", meta1.get("pair_count", 0))
        count2 = meta2.get("total", meta2.get("pair_count", 0))
        q1 = meta1.get("avg_quality", 0.0)
        q2 = meta2.get("avg_quality", 0.0)
        d1_domains = meta1.get("domains", {})
        d2_domains = meta2.get("domains", {})
        domains1 = set(d1_domains)
        domains2 = set(d2_domains)

        domains_changed = {}
        for domain in domains1 & domains2:
            delta = d2_domains.get(domain, 0) - d1_domains.get(domain, 0)
            if delta != 0:
                domains_changed[domain] = delta

        return {
            "v1": v1,
            "v2": v2,
            "pair_count_delta": count2 - count1,
            "quality_delta": round(q2 - q1, 4),
            "domains_added": sorted(domains2 - domains1),
            "domains_removed": sorted(domains1 - domains2),
            "domains_changed": domains_changed,
        }

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_datetime(value: str | datetime | None) -> datetime:
        """Parse a datetime from string, passthrough datetime, or return now(UTC)."""
        if isinstance(value, datetime):
            return value
        if isinstance(value, str) and value:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                pass
        return datetime.now(timezone.utc)

    def _read_metadata(self, version_dir: Path) -> dict | None:
        """Read metadata.yaml (or .json fallback) from a version directory."""
        yaml_path = version_dir / "metadata.yaml"
        json_path = version_dir / "metadata.json"

        if yaml_path.exists():
            try:
                import yaml

                return yaml.safe_load(yaml_path.read_text()) or {}
            except Exception:
                return {}

        if json_path.exists():
            try:
                return json.loads(json_path.read_text())
            except Exception:
                return {}

        return None

    @classmethod
    def _meta_to_version(
        cls, meta: dict, version_name: str, tenant_id: str
    ) -> DatasetVersion:
        """Convert metadata dict to DatasetVersion dataclass."""
        return DatasetVersion(
            version=version_name,
            created_at=cls._parse_datetime(meta.get("created_at")),
            pair_count=meta.get("total", meta.get("pair_count", 0)),
            domains=meta.get("domains", {}),
            avg_quality=meta.get("avg_quality", 0.0),
            tenant_id=tenant_id,
            notes=meta.get("notes", ""),
        )
