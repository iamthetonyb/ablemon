"""Tests for D13 — Inline Diff Previews + Stale File Detection.

Covers: diff generation, stale detection, read tracking, edge cases.
"""

import os
import time
import pytest

from able.tools.diff_preview import (
    DiffResult,
    FileTracker,
    StaleWarning,
)


@pytest.fixture
def tracker():
    return FileTracker()


# ── Diff generation ──────────────────────────────────────────────

class TestDiffGeneration:

    def test_basic_diff(self):
        old = "line1\nline2\nline3\n"
        new = "line1\nmodified\nline3\n"
        result = FileTracker.generate_diff("test.py", old, new)
        assert isinstance(result, DiffResult)
        assert result.has_changes
        assert result.lines_added >= 1
        assert result.lines_removed >= 1
        assert "modified" in result.unified_diff

    def test_no_changes(self):
        content = "same content\n"
        result = FileTracker.generate_diff("test.py", content, content)
        assert not result.has_changes
        assert result.unified_diff == ""

    def test_addition_only(self):
        old = "line1\n"
        new = "line1\nline2\nline3\n"
        result = FileTracker.generate_diff("test.py", old, new)
        assert result.lines_added == 2
        assert result.lines_removed == 0

    def test_deletion_only(self):
        old = "line1\nline2\nline3\n"
        new = "line1\n"
        result = FileTracker.generate_diff("test.py", old, new)
        assert result.lines_removed == 2
        assert result.lines_added == 0

    def test_diff_header_contains_path(self):
        result = FileTracker.generate_diff("src/auth.py", "old\n", "new\n")
        assert "a/src/auth.py" in result.unified_diff
        assert "b/src/auth.py" in result.unified_diff

    def test_empty_to_content(self):
        result = FileTracker.generate_diff("new.py", "", "print('hello')\n")
        assert result.has_changes
        assert result.lines_added >= 1

    def test_content_to_empty(self):
        result = FileTracker.generate_diff("del.py", "print('hello')\n", "")
        assert result.has_changes
        assert result.lines_removed >= 1

    def test_summary(self):
        result = FileTracker.generate_diff("test.py", "a\n", "b\n")
        s = result.summary()
        assert "test.py" in s

    def test_summary_no_changes(self):
        result = FileTracker.generate_diff("test.py", "a\n", "a\n")
        assert "no changes" in result.summary()


# ── Read tracking ────────────────────────────────────────────────

class TestReadTracking:

    def test_record_read(self, tracker, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        record = tracker.record_read(str(f))
        assert record.mtime_at_read > 0
        assert record.size_at_read == 5

    def test_record_read_nonexistent(self, tracker, tmp_path):
        record = tracker.record_read(str(tmp_path / "nope.txt"))
        assert record.mtime_at_read == 0

    def test_tracked_files(self, tracker, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("a")
        f2.write_text("b")
        tracker.record_read(str(f1))
        tracker.record_read(str(f2))
        assert len(tracker.tracked_files()) == 2

    def test_clear(self, tracker, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("x")
        tracker.record_read(str(f))
        tracker.clear()
        assert len(tracker.tracked_files()) == 0


# ── Stale detection ──────────────────────────────────────────────

class TestStaleDetection:

    def test_not_stale_immediately(self, tracker, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("original")
        tracker.record_read(str(f))
        assert tracker.check_stale(str(f)) is None

    def test_stale_after_external_modification(self, tracker, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("original")
        record = tracker.record_read(str(f))
        # Simulate external modification by setting mtime in the future
        future_time = record.mtime_at_read + 10
        os.utime(str(f), (future_time, future_time))
        warning = tracker.check_stale(str(f))
        assert warning is not None
        assert isinstance(warning, StaleWarning)
        assert "modified externally" in warning.message

    def test_not_stale_if_never_read(self, tracker, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("never read")
        assert tracker.check_stale(str(f)) is None

    def test_not_stale_if_file_deleted(self, tracker, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("will delete")
        tracker.record_read(str(f))
        f.unlink()
        assert tracker.check_stale(str(f)) is None
