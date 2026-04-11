"""Tests for D13 — Inline Diff Previews + Stale File Detection.

Covers: read tracking, stale detection, diff generation, write with tracking,
LRU eviction, edge cases.
"""

import os
import time
import pytest
from pathlib import Path

from able.tools.file_tracker import (
    DiffResult,
    FileSnapshot,
    FileTracker,
    StaleCheck,
)


@pytest.fixture
def tracker():
    return FileTracker(max_tracked=10)


@pytest.fixture
def tmp_file(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("line 1\nline 2\nline 3\n")
    return f


# ── Read tracking ─────────────────────────────────────────────


class TestRecordRead:

    def test_records_mtime(self, tracker, tmp_file):
        snap = tracker.record_read(str(tmp_file))
        assert snap.mtime > 0
        assert snap.content == "line 1\nline 2\nline 3\n"

    def test_records_size(self, tracker, tmp_file):
        snap = tracker.record_read(str(tmp_file))
        assert snap.size > 0

    def test_uses_provided_content(self, tracker, tmp_file):
        snap = tracker.record_read(str(tmp_file), content="override")
        assert snap.content == "override"

    def test_missing_file(self, tracker, tmp_path):
        snap = tracker.record_read(str(tmp_path / "ghost.txt"))
        assert snap.mtime == 0.0
        assert snap.content == ""

    def test_tracked_count(self, tracker, tmp_file):
        tracker.record_read(str(tmp_file))
        assert tracker.tracked_count == 1

    def test_tracked_files(self, tracker, tmp_file):
        tracker.record_read(str(tmp_file))
        assert len(tracker.tracked_files) == 1


# ── Stale detection ───────────────────────────────────────────


class TestStaleDetection:

    def test_not_stale_immediately(self, tracker, tmp_file):
        tracker.record_read(str(tmp_file))
        check = tracker.check_stale(str(tmp_file))
        assert not check.is_stale

    def test_stale_after_external_write(self, tracker, tmp_file):
        tracker.record_read(str(tmp_file))
        # Modify the file externally
        time.sleep(0.05)
        tmp_file.write_text("modified externally\n")
        check = tracker.check_stale(str(tmp_file))
        assert check.is_stale
        assert "externally" in check.message.lower()

    def test_untracked_file(self, tracker, tmp_file):
        check = tracker.check_stale(str(tmp_file))
        assert not check.is_stale
        assert "not tracked" in check.message.lower()

    def test_deleted_file(self, tracker, tmp_file):
        tracker.record_read(str(tmp_file))
        tmp_file.unlink()
        check = tracker.check_stale(str(tmp_file))
        assert check.is_stale


# ── Diff generation ───────────────────────────────────────────


class TestDiffGeneration:

    def test_diff_with_changes(self, tracker, tmp_file):
        tracker.record_read(str(tmp_file))
        diff = tracker.generate_diff(str(tmp_file), "line 1\nline 2 MODIFIED\nline 3\n")
        assert diff.has_changes
        assert diff.additions > 0
        assert diff.deletions > 0
        assert not diff.is_new

    def test_diff_no_changes(self, tracker, tmp_file):
        tracker.record_read(str(tmp_file))
        diff = tracker.generate_diff(str(tmp_file), "line 1\nline 2\nline 3\n")
        assert not diff.has_changes

    def test_diff_new_file(self, tracker, tmp_path):
        diff = tracker.generate_diff(str(tmp_path / "new.txt"), "new content\n")
        assert diff.is_new
        assert diff.has_changes

    def test_diff_summary(self, tracker, tmp_file):
        tracker.record_read(str(tmp_file))
        diff = tracker.generate_diff(str(tmp_file), "replaced\n")
        s = diff.summary()
        assert "+" in s or "-" in s

    def test_diff_as_text(self, tracker, tmp_file):
        tracker.record_read(str(tmp_file))
        diff = tracker.generate_diff(str(tmp_file), "line 1\nchanged\nline 3\n")
        text = diff.as_text()
        assert "---" in text or "+++" in text


# ── Write with tracking ──────────────────────────────────────


class TestWriteWithTracking:

    def test_write_generates_diff(self, tracker, tmp_file):
        tracker.record_read(str(tmp_file))
        diff, stale = tracker.write_with_tracking(
            str(tmp_file), "new content\n"
        )
        assert diff.has_changes
        assert stale is None
        # File should be updated
        assert tmp_file.read_text() == "new content\n"

    def test_write_blocks_on_stale(self, tracker, tmp_file):
        tracker.record_read(str(tmp_file))
        time.sleep(0.05)
        tmp_file.write_text("external edit\n")
        diff, stale = tracker.write_with_tracking(
            str(tmp_file), "my write\n"
        )
        assert stale is not None
        assert stale.is_stale
        # File should NOT be overwritten
        assert tmp_file.read_text() == "external edit\n"

    def test_write_force_bypasses_stale(self, tracker, tmp_file):
        tracker.record_read(str(tmp_file))
        time.sleep(0.05)
        tmp_file.write_text("external edit\n")
        diff, stale = tracker.write_with_tracking(
            str(tmp_file), "forced write\n", force=True
        )
        assert stale is None
        assert tmp_file.read_text() == "forced write\n"

    def test_write_updates_snapshot(self, tracker, tmp_file):
        tracker.record_read(str(tmp_file))
        tracker.write_with_tracking(str(tmp_file), "updated\n")
        # Second write should not be stale
        diff, stale = tracker.write_with_tracking(str(tmp_file), "updated again\n")
        assert stale is None


# ── Eviction ──────────────────────────────────────────────────


class TestEviction:

    def test_evicts_oldest_when_full(self, tmp_path):
        tracker = FileTracker(max_tracked=3)
        files = []
        for i in range(5):
            f = tmp_path / f"file{i}.txt"
            f.write_text(f"content {i}")
            files.append(f)
            tracker.record_read(str(f))
        assert tracker.tracked_count == 3

    def test_forget(self, tracker, tmp_file):
        tracker.record_read(str(tmp_file))
        assert tracker.tracked_count == 1
        tracker.forget(str(tmp_file))
        assert tracker.tracked_count == 0

    def test_clear(self, tracker, tmp_path):
        for i in range(3):
            f = tmp_path / f"f{i}.txt"
            f.write_text("x")
            tracker.record_read(str(f))
        tracker.clear()
        assert tracker.tracked_count == 0
