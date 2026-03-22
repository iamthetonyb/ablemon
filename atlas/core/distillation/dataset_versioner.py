"""
Dataset Versioner — Git-based versioning for training datasets.

Uses the git_trail.py pattern for audit trail. Each corpus version
is stored in a numbered directory with train/val/test splits and metadata.

Storage layout:
    ~/.atlas/distillation/corpus/{tenant_id}/
    +-- v001/
    |   +-- train.jsonl
    |   +-- val.jsonl
    |   +-- test.jsonl
    |   +-- metadata.yaml
    +-- v002/
    +-- latest -> v002/

Usage:
    from atlas.core.distillation.dataset_versioner import DatasetVersioner

    versioner = DatasetVersioner()
    version = await versioner.create_version(
        tenant_id="tony",
        train_path=Path("/tmp/train.jsonl"),
        val_path=Path("/tmp/val.jsonl"),
        test_path=Path("/tmp/test.jsonl"),
        metadata={"examples": 1500, "tier": "seed"},
    )
"""

import asyncio
import json
import logging
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False
    logger.debug("PyYAML not installed — metadata will use JSON fallback")

DEFAULT_CORPUS_ROOT = Path("~/.atlas/distillation/corpus").expanduser()


@dataclass
class VersionInfo:
    """Metadata for a dataset version."""

    version: int = 0
    version_tag: str = ""  # e.g. "v001"
    tenant_id: str = "tony"
    created_at: str = ""
    example_count: int = 0
    train_count: int = 0
    val_count: int = 0
    test_count: int = 0
    corpus_tier: str = ""  # seed, growth, full
    domains: Dict[str, int] = field(default_factory=dict)
    quality_threshold: float = 0.8
    filters_applied: List[str] = field(default_factory=list)
    source: str = ""  # "nightly", "full", "manual"
    notes: str = ""
    path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DatasetVersioner:
    """
    Git-based versioning for training datasets.

    Uses the git_trail.py pattern for audit trail. Each version
    is an immutable snapshot with train/val/test splits.

    Storage layout:
        {corpus_root}/{tenant_id}/
        +-- v001/
        |   +-- train.jsonl
        |   +-- val.jsonl
        |   +-- test.jsonl
        |   +-- metadata.yaml
        +-- v002/
        +-- latest -> v002/
    """

    def __init__(
        self,
        corpus_root: Optional[Path] = None,
        auto_audit: bool = True,
    ):
        """
        Args:
            corpus_root: Base directory for all corpus versions.
                         Defaults to ~/.atlas/distillation/corpus/
            auto_audit: If True, record actions via git_trail (when available)
        """
        self.corpus_root = Path(corpus_root) if corpus_root else DEFAULT_CORPUS_ROOT
        self.auto_audit = auto_audit
        self._trail = None

    def _tenant_dir(self, tenant_id: str) -> Path:
        """Get the directory for a tenant's corpus versions."""
        return self.corpus_root / tenant_id

    def _get_trail(self):
        """Lazy-load GitAuditTrail if available."""
        if self._trail is not None:
            return self._trail
        if not self.auto_audit:
            return None
        try:
            from atlas.audit.git_trail import GitAuditTrail
            self._trail = GitAuditTrail(self.corpus_root, auto_commit=False)
        except (ImportError, Exception) as e:
            logger.debug(f"GitAuditTrail not available: {e}")
            self._trail = None
        return self._trail

    def get_next_version(self, tenant_id: str = "tony") -> int:
        """
        Determine the next version number for a tenant.

        Scans existing vNNN directories and returns max + 1.
        """
        tenant_dir = self._tenant_dir(tenant_id)
        if not tenant_dir.exists():
            return 1

        max_version = 0
        for child in tenant_dir.iterdir():
            if child.is_dir() and child.name.startswith("v"):
                try:
                    v = int(child.name[1:])
                    max_version = max(max_version, v)
                except ValueError:
                    continue
        return max_version + 1

    def _version_tag(self, version: int) -> str:
        """Format version number as vNNN tag."""
        return f"v{version:03d}"

    async def create_version(
        self,
        tenant_id: str,
        train_path: Path,
        val_path: Path,
        test_path: Path,
        metadata: Optional[Dict[str, Any]] = None,
        source: str = "manual",
    ) -> VersionInfo:
        """
        Create a new versioned snapshot of a training dataset.

        Copies the train/val/test JSONL files into a new vNNN directory
        and writes metadata. Updates the 'latest' symlink.

        Args:
            tenant_id: Tenant identifier for isolation
            train_path: Path to training split JSONL
            val_path: Path to validation split JSONL
            test_path: Path to test split JSONL
            metadata: Additional metadata to store
            source: How this version was created ("nightly", "full", "manual")

        Returns:
            VersionInfo with the new version details
        """
        version_num = self.get_next_version(tenant_id)
        tag = self._version_tag(version_num)
        tenant_dir = self._tenant_dir(tenant_id)
        version_dir = tenant_dir / tag
        version_dir.mkdir(parents=True, exist_ok=True)

        # Copy files into version directory (parallel)
        loop = asyncio.get_event_loop()
        dest_train = version_dir / "train.jsonl"
        dest_val = version_dir / "val.jsonl"
        dest_test = version_dir / "test.jsonl"

        await asyncio.gather(
            loop.run_in_executor(None, shutil.copy2, str(train_path), str(dest_train)),
            loop.run_in_executor(None, shutil.copy2, str(val_path), str(dest_val)),
            loop.run_in_executor(None, shutil.copy2, str(test_path), str(dest_test)),
        )

        # Count lines in each file (parallel)
        train_count, val_count, test_count = await asyncio.gather(
            self._count_lines(dest_train),
            self._count_lines(dest_val),
            self._count_lines(dest_test),
        )
        total = train_count + val_count + test_count

        # Build version info
        info = VersionInfo(
            version=version_num,
            version_tag=tag,
            tenant_id=tenant_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            example_count=total,
            train_count=train_count,
            val_count=val_count,
            test_count=test_count,
            source=source,
            path=str(version_dir),
        )

        # Merge extra metadata
        if metadata:
            for key, value in metadata.items():
                if hasattr(info, key):
                    setattr(info, key, value)

        # Write metadata file
        meta_path = version_dir / "metadata.yaml"
        await self._write_metadata(meta_path, info)

        # Update 'latest' symlink
        await self._update_latest_symlink(tenant_dir, tag)

        # Audit trail
        trail = self._get_trail()
        if trail:
            try:
                await trail.record_action(
                    "dataset_version_created",
                    {
                        "summary": f"Created {tag} for tenant {tenant_id} ({total} examples)",
                        "version": version_num,
                        "tenant_id": tenant_id,
                        "total_examples": total,
                        "source": source,
                    },
                    files_changed=[dest_train, dest_val, dest_test, meta_path],
                )
            except Exception as e:
                logger.debug(f"Audit trail write failed: {e}")

        logger.info(
            f"Created dataset version {tag} for tenant '{tenant_id}': "
            f"{total} examples (train={train_count}, val={val_count}, test={test_count})"
        )

        return info

    async def get_version(
        self,
        tenant_id: str,
        version: Optional[int] = None,
    ) -> Optional[VersionInfo]:
        """
        Get metadata for a specific version, or latest if version is None.

        Args:
            tenant_id: Tenant identifier
            version: Version number (None = latest)

        Returns:
            VersionInfo or None if version doesn't exist
        """
        tenant_dir = self._tenant_dir(tenant_id)

        if version is None:
            # Resolve 'latest' symlink
            latest = tenant_dir / "latest"
            if latest.is_symlink() or latest.exists():
                target = latest.resolve()
                if target.exists():
                    return await self._load_metadata(target)
            # Fallback: find highest version
            version = self.get_next_version(tenant_id) - 1
            if version < 1:
                return None

        tag = self._version_tag(version)
        version_dir = tenant_dir / tag
        if not version_dir.exists():
            return None

        return await self._load_metadata(version_dir)

    async def list_versions(self, tenant_id: str = "tony") -> List[VersionInfo]:
        """
        List all versions for a tenant, sorted by version number.

        Args:
            tenant_id: Tenant identifier

        Returns:
            List of VersionInfo, oldest first
        """
        tenant_dir = self._tenant_dir(tenant_id)
        if not tenant_dir.exists():
            return []

        versions = []
        for child in sorted(tenant_dir.iterdir()):
            if child.is_dir() and child.name.startswith("v"):
                try:
                    int(child.name[1:])
                except ValueError:
                    continue
                info = await self._load_metadata(child)
                if info:
                    versions.append(info)

        return versions

    async def delete_version(
        self,
        tenant_id: str,
        version: int,
    ) -> bool:
        """
        Delete a specific version. Won't delete the latest version.

        Args:
            tenant_id: Tenant identifier
            version: Version number to delete

        Returns:
            True if deleted
        """
        latest_info = await self.get_version(tenant_id)
        if latest_info and latest_info.version == version:
            logger.warning(f"Refusing to delete latest version {version}")
            return False

        tag = self._version_tag(version)
        version_dir = self._tenant_dir(tenant_id) / tag
        if not version_dir.exists():
            return False

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, shutil.rmtree, str(version_dir))

        trail = self._get_trail()
        if trail:
            try:
                await trail.record_action(
                    "dataset_version_deleted",
                    {
                        "summary": f"Deleted {tag} for tenant {tenant_id}",
                        "version": version,
                        "tenant_id": tenant_id,
                    },
                )
            except Exception:
                pass

        logger.info(f"Deleted dataset version {tag} for tenant '{tenant_id}'")
        return True

    async def get_latest_path(self, tenant_id: str = "tony") -> Optional[Path]:
        """
        Get the filesystem path to the latest version directory.

        Args:
            tenant_id: Tenant identifier

        Returns:
            Path to latest version directory, or None
        """
        info = await self.get_version(tenant_id)
        if info and info.path:
            p = Path(info.path)
            if p.exists():
                return p
        return None

    # ── Private helpers ────────────────────────────────────────────────

    async def _count_lines(self, path: Path) -> int:
        """Count non-empty lines in a file."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_count_lines, path)

    @staticmethod
    def _sync_count_lines(path: Path) -> int:
        count = 0
        with open(path, "r") as f:
            for line in f:
                if line.strip():
                    count += 1
        return count

    async def _write_metadata(self, path: Path, info: VersionInfo) -> None:
        """Write metadata as YAML (or JSON fallback)."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_write_metadata, path, info)

    @staticmethod
    def _sync_write_metadata(path: Path, info: VersionInfo) -> None:
        data = info.to_dict()
        if YAML_AVAILABLE:
            with open(path, "w") as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        else:
            # Fallback to JSON
            json_path = path.with_suffix(".json")
            with open(json_path, "w") as f:
                json.dump(data, f, indent=2)

    async def _load_metadata(self, version_dir: Path) -> Optional[VersionInfo]:
        """Load metadata from a version directory."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._sync_load_metadata, version_dir
        )

    @staticmethod
    def _sync_load_metadata(version_dir: Path) -> Optional[VersionInfo]:
        meta_yaml = version_dir / "metadata.yaml"
        meta_json = version_dir / "metadata.json"

        data = None
        if meta_yaml.exists() and YAML_AVAILABLE:
            with open(meta_yaml) as f:
                data = yaml.safe_load(f)
        elif meta_json.exists():
            with open(meta_json) as f:
                data = json.load(f)

        if not data:
            # Build minimal info from directory name
            try:
                version_num = int(version_dir.name[1:])
            except ValueError:
                return None
            return VersionInfo(
                version=version_num,
                version_tag=version_dir.name,
                path=str(version_dir),
            )

        # Map dict to VersionInfo, ignoring unknown keys
        known_fields = {f.name for f in VersionInfo.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return VersionInfo(**filtered)

    async def _update_latest_symlink(self, tenant_dir: Path, tag: str) -> None:
        """Update the 'latest' symlink to point to the given version tag."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self._sync_update_symlink, tenant_dir, tag
        )

    @staticmethod
    def _sync_update_symlink(tenant_dir: Path, tag: str) -> None:
        latest = tenant_dir / "latest"
        # Remove existing symlink or directory
        if latest.is_symlink() or latest.exists():
            if latest.is_symlink():
                latest.unlink()
            else:
                # Not a symlink — don't delete a real directory
                logger.warning(f"'latest' exists but is not a symlink: {latest}")
                return

        try:
            latest.symlink_to(tag)
        except OSError as e:
            logger.warning(f"Failed to create symlink: {e}")
