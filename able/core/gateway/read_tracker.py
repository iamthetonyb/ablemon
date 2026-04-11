"""
Read-Before-Write enforcement (Wove pattern).

Prevents the AI from editing or overwriting a file it hasn't read first.
Not a prompt instruction — an actual tool-level rejection.

New files are exempt. Only existing files require a prior read.
"""

from __future__ import annotations

import os
from collections import OrderedDict
from threading import Lock

# Max tracked paths to prevent unbounded memory growth
_MAX_TRACKED = 500


class ReadTracker:
    """Track file reads per session. Gate writes on prior reads.

    Usage::

        tracker = ReadTracker()
        tracker.record_read("/path/to/file.py")
        ok, msg = tracker.check_write("/path/to/file.py")
        # ok=True since we read it

        ok, msg = tracker.check_write("/path/to/other.py")
        # ok=False — haven't read other.py yet
    """

    def __init__(self, max_tracked: int = _MAX_TRACKED) -> None:
        self._reads: OrderedDict[str, bool] = OrderedDict()
        self._max = max_tracked
        self._lock = Lock()

    def record_read(self, path: str) -> None:
        """Record that a file was read in this session."""
        canonical = self._canonical(path)
        with self._lock:
            self._reads[canonical] = True
            self._reads.move_to_end(canonical)
            # LRU eviction
            while len(self._reads) > self._max:
                self._reads.popitem(last=False)

    def check_write(self, path: str) -> tuple[bool, str]:
        """Check if a file write is allowed.

        Returns (allowed, message). New files always allowed.
        Existing files require a prior read.
        """
        canonical = self._canonical(path)

        # New file — always allowed
        if not os.path.exists(canonical):
            return True, ""

        with self._lock:
            if canonical in self._reads:
                return True, ""

        return False, (
            f"You must read '{path}' before overwriting it. "
            "Use read_file first to understand the existing content."
        )

    def check_write_large(self, path: str, new_content: str,
                          max_lines: int = 200) -> tuple[bool, str]:
        """Check if a full-file rewrite should be blocked for large files.

        Files over max_lines lines should use targeted edits, not full rewrites.
        """
        if not os.path.exists(path):
            return True, ""

        try:
            with open(path, "r", errors="replace") as f:
                line_count = sum(1 for _ in f)
        except OSError:
            return True, ""

        if line_count > max_lines:
            return False, (
                f"File '{path}' has {line_count} lines (>{max_lines}). "
                "Do NOT rewrite the entire file. Use targeted edits instead."
            )
        return True, ""

    def was_read(self, path: str) -> bool:
        """Check if a file was previously read."""
        canonical = self._canonical(path)
        with self._lock:
            return canonical in self._reads

    def clear(self) -> None:
        """Clear all tracked reads (session reset)."""
        with self._lock:
            self._reads.clear()

    @property
    def tracked_count(self) -> int:
        """Number of files currently tracked."""
        return len(self._reads)

    @staticmethod
    def _canonical(path: str) -> str:
        """Resolve to canonical path, following symlinks."""
        try:
            return os.path.realpath(os.path.expanduser(path))
        except (OSError, ValueError):
            return os.path.abspath(path)
