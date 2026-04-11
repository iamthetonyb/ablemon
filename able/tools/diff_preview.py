"""
D13 — Inline Diff Previews + Stale File Detection.

After file write: generates a unified diff between old and new content.
Before file write: checks mtime to detect external modifications since
the last read, warning before overwrite.

Forked from Hermes v0.7 PR #4411 + #4345.

Usage:
    tracker = FileTracker()
    tracker.record_read("auth.py")

    # Before writing:
    warning = tracker.check_stale("auth.py")
    if warning:
        print(f"WARNING: {warning}")

    # After writing:
    diff = tracker.generate_diff("auth.py", old_content, new_content)
    print(diff)
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


@dataclass
class FileReadRecord:
    """Record of when a file was last read."""
    path: str
    mtime_at_read: float  # os.path.getmtime() at read time
    read_time: float      # time.time() when we read it
    size_at_read: int = 0


@dataclass
class DiffResult:
    """Result of a diff operation."""
    path: str
    unified_diff: str
    lines_added: int = 0
    lines_removed: int = 0
    lines_changed: int = 0

    @property
    def has_changes(self) -> bool:
        return bool(self.unified_diff)

    def summary(self) -> str:
        if not self.has_changes:
            return f"{self.path}: no changes"
        return f"{self.path}: +{self.lines_added} -{self.lines_removed}"


@dataclass
class StaleWarning:
    """Warning that a file was modified externally."""
    path: str
    mtime_at_read: float
    mtime_now: float
    message: str


class FileTracker:
    """Track file reads and detect stale state before writes.

    Records when files are read and their mtime at that point.
    Before writing, compares current mtime to detect external
    modifications that would be overwritten.
    """

    def __init__(self):
        self._reads: Dict[str, FileReadRecord] = {}

    def record_read(self, path: str) -> FileReadRecord:
        """Record that a file was read at the current time.

        Args:
            path: File path (will be normalized to absolute).

        Returns:
            The read record.
        """
        abs_path = str(Path(path).resolve())
        try:
            stat = os.stat(abs_path)
            mtime = stat.st_mtime
            size = stat.st_size
        except OSError:
            mtime = 0
            size = 0

        record = FileReadRecord(
            path=abs_path,
            mtime_at_read=mtime,
            read_time=time.time(),
            size_at_read=size,
        )
        self._reads[abs_path] = record
        return record

    def check_stale(self, path: str) -> Optional[StaleWarning]:
        """Check if a file has been modified externally since our last read.

        Args:
            path: File path to check.

        Returns:
            StaleWarning if the file changed externally, None if safe.
        """
        abs_path = str(Path(path).resolve())
        record = self._reads.get(abs_path)
        if record is None:
            return None  # Never read — can't be stale

        try:
            current_mtime = os.path.getmtime(abs_path)
        except OSError:
            return None  # File doesn't exist — nothing to conflict with

        if current_mtime > record.mtime_at_read:
            delta = current_mtime - record.mtime_at_read
            return StaleWarning(
                path=abs_path,
                mtime_at_read=record.mtime_at_read,
                mtime_now=current_mtime,
                message=(
                    f"File modified externally since last read "
                    f"({delta:.1f}s ago). Overwriting may lose changes."
                ),
            )
        return None

    def tracked_files(self) -> List[str]:
        """List all tracked file paths."""
        return list(self._reads.keys())

    def clear(self) -> None:
        """Clear all read records."""
        self._reads.clear()

    @staticmethod
    def generate_diff(
        path: str,
        old_content: str,
        new_content: str,
        context_lines: int = 3,
    ) -> DiffResult:
        """Generate a unified diff between old and new content.

        Args:
            path: File path (for display in diff header).
            old_content: Content before the write.
            new_content: Content after the write.
            context_lines: Number of context lines in diff.

        Returns:
            DiffResult with unified diff string and stats.
        """
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)

        diff_lines = list(difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=context_lines,
        ))

        added = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))

        return DiffResult(
            path=path,
            unified_diff="".join(diff_lines),
            lines_added=added,
            lines_removed=removed,
            lines_changed=min(added, removed),
        )
