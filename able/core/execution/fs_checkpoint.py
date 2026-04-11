"""
D15 — Filesystem Checkpoints + Rollback.

Snapshots files before write/delete operations, enabling rollback
to a prior state. Complements OvernightLoop's git-level rollback
with fine-grained file-level granularity.

Forked from Hermes v0.2 PR #824.

Usage:
    cp = FilesystemCheckpoint(session_id="abc123")
    cp.snapshot("src/auth.py")  # Save current state
    # ... write to auth.py ...
    cp.rollback("src/auth.py")  # Restore
    # or:
    cp.rollback_all()  # Restore everything
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_CHECKPOINT_DIR = "data/checkpoints"
_MAX_SNAPSHOTS_PER_FILE = 10  # Rolling window


@dataclass
class FileSnapshot:
    """A single snapshot of a file."""
    original_path: str
    snapshot_path: str
    timestamp: float
    size_bytes: int
    content_hash: str
    existed: bool  # False if file didn't exist (snapshot of absence)


@dataclass
class CheckpointStats:
    """Stats for a checkpoint session."""
    session_id: str
    files_tracked: int
    total_snapshots: int
    total_size_bytes: int
    rollbacks_performed: int


class FilesystemCheckpoint:
    """File-level checkpoint and rollback for a session.

    Snapshots are stored in data/checkpoints/{session_id}/ with
    content-addressed naming to avoid duplicates.
    """

    def __init__(
        self,
        session_id: str,
        checkpoint_dir: str = _DEFAULT_CHECKPOINT_DIR,
        max_snapshots: int = _MAX_SNAPSHOTS_PER_FILE,
    ):
        self.session_id = session_id
        self._base_dir = Path(checkpoint_dir) / session_id
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._max_snapshots = max_snapshots
        self._snapshots: Dict[str, List[FileSnapshot]] = {}  # path → [snapshots]
        self._rollback_count = 0

    def snapshot(self, file_path: str) -> Optional[FileSnapshot]:
        """Snapshot a file's current state before modification.

        Args:
            file_path: Path to the file to snapshot.

        Returns:
            FileSnapshot if successful, None on error.
        """
        src = Path(file_path)
        existed = src.exists()

        if existed:
            content = src.read_bytes()
            content_hash = hashlib.sha256(content).hexdigest()[:16]
            size = len(content)
        else:
            content_hash = "nonexistent"
            size = 0
            content = b""

        # Check for duplicate (same content hash)
        existing = self._snapshots.get(file_path, [])
        if existing and existing[-1].content_hash == content_hash:
            return existing[-1]  # No change since last snapshot

        # Build snapshot path
        ts = time.time()
        safe_name = file_path.replace("/", "__").replace("\\", "__")
        snap_name = f"{safe_name}.{content_hash}.{int(ts)}"
        snap_path = self._base_dir / snap_name

        try:
            if existed:
                shutil.copy2(str(src), str(snap_path))
            else:
                # Record that the file didn't exist (for rollback = delete)
                snap_path.write_text("")

            snap = FileSnapshot(
                original_path=file_path,
                snapshot_path=str(snap_path),
                timestamp=ts,
                size_bytes=size,
                content_hash=content_hash,
                existed=existed,
            )

            self._snapshots.setdefault(file_path, []).append(snap)

            # Enforce rolling window
            if len(self._snapshots[file_path]) > self._max_snapshots:
                old = self._snapshots[file_path].pop(0)
                try:
                    Path(old.snapshot_path).unlink(missing_ok=True)
                except Exception:
                    pass

            logger.debug(
                "Snapshot: %s → %s (%s, %d bytes)",
                file_path, snap_path, content_hash, size,
            )
            return snap

        except Exception as e:
            logger.error("Failed to snapshot %s: %s", file_path, e)
            return None

    def rollback(self, file_path: str, index: int = -1) -> bool:
        """Rollback a file to a previous snapshot.

        Args:
            file_path: Path to the file to rollback.
            index: Which snapshot to restore (-1 = most recent).

        Returns:
            True if rollback succeeded.
        """
        snapshots = self._snapshots.get(file_path, [])
        if not snapshots:
            logger.warning("No snapshots found for %s", file_path)
            return False

        try:
            snap = snapshots[index]
        except IndexError:
            logger.warning("Snapshot index %d out of range for %s", index, file_path)
            return False

        try:
            dst = Path(file_path)
            if snap.existed:
                snap_src = Path(snap.snapshot_path)
                if not snap_src.exists():
                    logger.error("Snapshot file missing: %s", snap.snapshot_path)
                    return False
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(snap_src), str(dst))
            else:
                # File didn't exist before — delete it
                if dst.exists():
                    dst.unlink()

            self._rollback_count += 1
            logger.info(
                "Rolled back %s to snapshot %s (hash=%s)",
                file_path, snap.snapshot_path, snap.content_hash,
            )
            return True

        except Exception as e:
            logger.error("Rollback failed for %s: %s", file_path, e)
            return False

    def rollback_all(self) -> Dict[str, bool]:
        """Rollback all tracked files to their most recent snapshots.

        Returns dict of {file_path: success}.
        """
        results = {}
        for file_path in self._snapshots:
            results[file_path] = self.rollback(file_path)
        return results

    def tracked_files(self) -> List[str]:
        """List all files with snapshots."""
        return list(self._snapshots.keys())

    def snapshot_count(self, file_path: Optional[str] = None) -> int:
        """Count snapshots for a file or all files."""
        if file_path:
            return len(self._snapshots.get(file_path, []))
        return sum(len(snaps) for snaps in self._snapshots.values())

    def stats(self) -> CheckpointStats:
        """Return checkpoint stats."""
        total_size = sum(
            s.size_bytes
            for snaps in self._snapshots.values()
            for s in snaps
        )
        return CheckpointStats(
            session_id=self.session_id,
            files_tracked=len(self._snapshots),
            total_snapshots=self.snapshot_count(),
            total_size_bytes=total_size,
            rollbacks_performed=self._rollback_count,
        )

    def cleanup(self) -> int:
        """Remove all snapshot files for this session.

        Returns count of files removed.
        """
        count = 0
        try:
            if self._base_dir.exists():
                for f in self._base_dir.iterdir():
                    f.unlink()
                    count += 1
                self._base_dir.rmdir()
        except Exception as e:
            logger.error("Cleanup error for session %s: %s", self.session_id, e)
        self._snapshots.clear()
        return count
