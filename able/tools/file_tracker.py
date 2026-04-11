"""
D13 — Inline Diff Previews + Stale File Detection.

Tracks file read/write operations to:
1. Generate unified diffs after file writes (for activity feed).
2. Detect stale files (modified externally since last read).

Forked from Hermes v0.7 PR #4411 + #4345 pattern.

Usage:
    tracker = FileTracker()
    content = tracker.read("src/main.py")        # Records mtime
    tracker.write("src/main.py", new_content)     # Generates diff, checks stale

    # Or check manually:
    if tracker.is_stale("src/main.py"):
        print("File modified externally since last read!")
"""

from __future__ import annotations

import difflib
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Max diff lines to retain (prevent OOM on huge files)
MAX_DIFF_LINES = 500
# Max tracked files (LRU eviction beyond this)
MAX_TRACKED_FILES = 200


@dataclass
class FileSnapshot:
    """Snapshot of a file at a point in time."""
    path: str
    mtime: float = 0.0
    content: str = ""
    size: int = 0
    read_at: float = 0.0


@dataclass
class DiffResult:
    """Result of comparing file versions."""
    path: str
    diff_lines: List[str] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    is_new: bool = False
    truncated: bool = False

    @property
    def has_changes(self) -> bool:
        return bool(self.diff_lines) or self.is_new

    def summary(self) -> str:
        if self.is_new:
            return f"[new file] {self.path}"
        return f"+{self.additions} -{self.deletions} {self.path}"

    def as_text(self) -> str:
        return "\n".join(self.diff_lines)


@dataclass
class StaleCheck:
    """Result of a stale file check."""
    path: str
    is_stale: bool = False
    read_mtime: float = 0.0
    current_mtime: float = 0.0
    message: str = ""


class FileTracker:
    """Tracks file reads/writes for diff generation and stale detection.

    Thread-safe for single-writer usage (typical agent pattern).
    """

    def __init__(self, max_tracked: int = MAX_TRACKED_FILES):
        self._snapshots: Dict[str, FileSnapshot] = {}
        self._max_tracked = max_tracked

    def record_read(
        self,
        path: str,
        content: Optional[str] = None,
    ) -> FileSnapshot:
        """Record a file read, capturing mtime and optionally content.

        Args:
            path: File path (resolved to absolute).
            content: File content (if already read). If None, reads from disk.

        Returns:
            FileSnapshot of the file at read time.
        """
        abs_path = str(Path(path).resolve())

        try:
            mtime = os.path.getmtime(abs_path)
            size = os.path.getsize(abs_path)
        except OSError:
            mtime = 0.0
            size = 0

        if content is None:
            try:
                content = Path(abs_path).read_text(errors="replace")
            except OSError:
                content = ""

        snapshot = FileSnapshot(
            path=abs_path,
            mtime=mtime,
            content=content,
            size=size,
            read_at=time.time(),
        )
        self._snapshots[abs_path] = snapshot
        self._evict_if_needed()
        return snapshot

    def check_stale(self, path: str) -> StaleCheck:
        """Check if a file has been modified since last read.

        Returns StaleCheck with is_stale=True if the file was modified
        externally after our last record_read().
        """
        abs_path = str(Path(path).resolve())
        snapshot = self._snapshots.get(abs_path)

        if not snapshot:
            return StaleCheck(
                path=abs_path,
                is_stale=False,
                message="File not tracked (no prior read)",
            )

        try:
            current_mtime = os.path.getmtime(abs_path)
        except OSError:
            return StaleCheck(
                path=abs_path,
                is_stale=True,
                read_mtime=snapshot.mtime,
                message="File no longer exists",
            )

        is_stale = current_mtime > snapshot.mtime
        return StaleCheck(
            path=abs_path,
            is_stale=is_stale,
            read_mtime=snapshot.mtime,
            current_mtime=current_mtime,
            message="Modified externally since last read" if is_stale else "OK",
        )

    def generate_diff(
        self,
        path: str,
        new_content: str,
        context_lines: int = 3,
    ) -> DiffResult:
        """Generate a unified diff between last-read content and new content.

        Args:
            path: File path.
            new_content: The new content being written.
            context_lines: Number of context lines in diff.

        Returns:
            DiffResult with diff lines and change counts.
        """
        abs_path = str(Path(path).resolve())
        snapshot = self._snapshots.get(abs_path)

        if not snapshot or not snapshot.content:
            return DiffResult(path=abs_path, is_new=True)

        old_lines = snapshot.content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)

        diff = list(difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{Path(abs_path).name}",
            tofile=f"b/{Path(abs_path).name}",
            n=context_lines,
        ))

        truncated = False
        if len(diff) > MAX_DIFF_LINES:
            diff = diff[:MAX_DIFF_LINES]
            diff.append(f"[... truncated, {len(diff)} more lines ...]\n")
            truncated = True

        additions = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
        deletions = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))

        return DiffResult(
            path=abs_path,
            diff_lines=[l.rstrip("\n") for l in diff],
            additions=additions,
            deletions=deletions,
            truncated=truncated,
        )

    def write_with_tracking(
        self,
        path: str,
        content: str,
        force: bool = False,
    ) -> tuple[DiffResult, Optional[StaleCheck]]:
        """Write a file with diff generation and stale detection.

        Args:
            path: File path.
            content: New content to write.
            force: If True, skip stale check and write anyway.

        Returns:
            (DiffResult, StaleCheck or None). StaleCheck is non-None
            only if the file was stale.
        """
        abs_path = str(Path(path).resolve())

        # Check stale
        stale = self.check_stale(abs_path)
        if stale.is_stale and not force:
            logger.warning(
                "Stale file detected: %s (mtime changed since read)", abs_path
            )
            # Return diff but don't write
            diff = self.generate_diff(abs_path, content)
            return diff, stale

        # Generate diff before write
        diff = self.generate_diff(abs_path, content)

        # Write the file
        Path(abs_path).write_text(content)

        # Update snapshot to new state
        self.record_read(abs_path, content)

        return diff, None

    @property
    def tracked_files(self) -> List[str]:
        return list(self._snapshots.keys())

    @property
    def tracked_count(self) -> int:
        return len(self._snapshots)

    def forget(self, path: str) -> bool:
        """Stop tracking a file."""
        abs_path = str(Path(path).resolve())
        return self._snapshots.pop(abs_path, None) is not None

    def clear(self) -> None:
        """Clear all tracked files."""
        self._snapshots.clear()

    def _evict_if_needed(self) -> None:
        """Evict oldest snapshots if over capacity."""
        if len(self._snapshots) <= self._max_tracked:
            return
        # Sort by read_at, evict oldest
        by_age = sorted(
            self._snapshots.items(),
            key=lambda x: x[1].read_at,
        )
        to_evict = len(self._snapshots) - self._max_tracked
        for path, _ in by_age[:to_evict]:
            del self._snapshots[path]
