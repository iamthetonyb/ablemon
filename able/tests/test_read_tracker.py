"""Tests for ReadTracker — read-before-write enforcement (Wove pattern)."""

import os
import pytest
from able.core.gateway.read_tracker import ReadTracker


class TestReadTracker:

    def test_new_file_always_allowed(self, tmp_path):
        tracker = ReadTracker()
        ok, msg = tracker.check_write(str(tmp_path / "nonexistent.py"))
        assert ok
        assert msg == ""

    def test_existing_file_blocked_without_read(self, tmp_path):
        f = tmp_path / "existing.py"
        f.write_text("content")
        tracker = ReadTracker()
        ok, msg = tracker.check_write(str(f))
        assert not ok
        assert "read" in msg.lower()

    def test_existing_file_allowed_after_read(self, tmp_path):
        f = tmp_path / "existing.py"
        f.write_text("content")
        tracker = ReadTracker()
        tracker.record_read(str(f))
        ok, msg = tracker.check_write(str(f))
        assert ok

    def test_was_read(self, tmp_path):
        f = tmp_path / "file.py"
        f.write_text("x")
        tracker = ReadTracker()
        assert not tracker.was_read(str(f))
        tracker.record_read(str(f))
        assert tracker.was_read(str(f))

    def test_clear(self, tmp_path):
        f = tmp_path / "file.py"
        f.write_text("x")
        tracker = ReadTracker()
        tracker.record_read(str(f))
        assert tracker.tracked_count == 1
        tracker.clear()
        assert tracker.tracked_count == 0

    def test_lru_eviction(self, tmp_path):
        tracker = ReadTracker(max_tracked=3)
        for i in range(5):
            f = tmp_path / f"file{i}.py"
            f.write_text("x")
            tracker.record_read(str(f))
        assert tracker.tracked_count == 3

    def test_canonical_resolves_symlinks(self, tmp_path):
        real = tmp_path / "real.py"
        real.write_text("content")
        link = tmp_path / "link.py"
        link.symlink_to(real)
        tracker = ReadTracker()
        tracker.record_read(str(link))
        assert tracker.was_read(str(real))

    def test_large_file_protection_blocks(self, tmp_path):
        f = tmp_path / "big.py"
        f.write_text("\n".join(f"line {i}" for i in range(300)))
        tracker = ReadTracker()
        ok, msg = tracker.check_write_large(str(f), "new content", max_lines=200)
        assert not ok
        assert "targeted edits" in msg.lower()

    def test_large_file_protection_allows_small(self, tmp_path):
        f = tmp_path / "small.py"
        f.write_text("line 1\nline 2\n")
        tracker = ReadTracker()
        ok, msg = tracker.check_write_large(str(f), "new content", max_lines=200)
        assert ok

    def test_large_file_protection_new_file(self, tmp_path):
        tracker = ReadTracker()
        ok, _ = tracker.check_write_large(
            str(tmp_path / "new.py"), "x" * 10000, max_lines=5
        )
        assert ok
