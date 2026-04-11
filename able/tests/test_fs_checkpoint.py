"""Tests for D15 — Filesystem Checkpoints + Rollback.

Covers: snapshot, rollback, rollback_all, dedup, rolling window,
nonexistent files, cleanup, stats.
"""

import pytest

from able.core.execution.fs_checkpoint import (
    FilesystemCheckpoint,
    FileSnapshot,
    CheckpointStats,
)


@pytest.fixture
def cp(tmp_path):
    return FilesystemCheckpoint(
        session_id="test-session",
        checkpoint_dir=str(tmp_path / "checkpoints"),
    )


# ── Snapshot ─────────────────────────────────────────────────────

class TestSnapshot:

    def test_snapshot_existing_file(self, cp, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("original content")
        snap = cp.snapshot(str(f))
        assert snap is not None
        assert snap.existed is True
        assert snap.size_bytes == len("original content")

    def test_snapshot_nonexistent_file(self, cp, tmp_path):
        snap = cp.snapshot(str(tmp_path / "nope.txt"))
        assert snap is not None
        assert snap.existed is False
        assert snap.content_hash == "nonexistent"

    def test_snapshot_deduplicates(self, cp, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("same content")
        snap1 = cp.snapshot(str(f))
        snap2 = cp.snapshot(str(f))
        # Same content → same snapshot returned
        assert snap1.content_hash == snap2.content_hash
        assert cp.snapshot_count(str(f)) == 1

    def test_snapshot_detects_change(self, cp, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("version 1")
        cp.snapshot(str(f))
        f.write_text("version 2")
        cp.snapshot(str(f))
        assert cp.snapshot_count(str(f)) == 2

    def test_snapshot_rolling_window(self, tmp_path):
        cp = FilesystemCheckpoint(
            session_id="test",
            checkpoint_dir=str(tmp_path / "cp"),
            max_snapshots=3,
        )
        f = tmp_path / "test.txt"
        for i in range(5):
            f.write_text(f"version {i}")
            cp.snapshot(str(f))
        assert cp.snapshot_count(str(f)) == 3


# ── Rollback ─────────────────────────────────────────────────────

class TestRollback:

    def test_rollback_restores_content(self, cp, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("original")
        cp.snapshot(str(f))
        f.write_text("modified")
        assert cp.rollback(str(f)) is True
        assert f.read_text() == "original"

    def test_rollback_to_nonexistence(self, cp, tmp_path):
        f = tmp_path / "new.txt"
        # Snapshot when file doesn't exist
        cp.snapshot(str(f))
        # Create the file
        f.write_text("should be deleted")
        # Rollback to nonexistence
        assert cp.rollback(str(f)) is True
        assert not f.exists()

    def test_rollback_no_snapshots(self, cp, tmp_path):
        assert cp.rollback(str(tmp_path / "never-snapped.txt")) is False

    def test_rollback_invalid_index(self, cp, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("x")
        cp.snapshot(str(f))
        assert cp.rollback(str(f), index=5) is False

    def test_rollback_all(self, cp, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("orig-a")
        f2.write_text("orig-b")
        cp.snapshot(str(f1))
        cp.snapshot(str(f2))
        f1.write_text("modified-a")
        f2.write_text("modified-b")
        results = cp.rollback_all()
        assert results[str(f1)] is True
        assert results[str(f2)] is True
        assert f1.read_text() == "orig-a"
        assert f2.read_text() == "orig-b"


# ── Stats & tracking ────────────────────────────────────────────

class TestStatsAndTracking:

    def test_tracked_files(self, cp, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("a")
        f2.write_text("b")
        cp.snapshot(str(f1))
        cp.snapshot(str(f2))
        assert len(cp.tracked_files()) == 2

    def test_stats(self, cp, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("content")
        cp.snapshot(str(f))
        stats = cp.stats()
        assert isinstance(stats, CheckpointStats)
        assert stats.session_id == "test-session"
        assert stats.files_tracked == 1
        assert stats.total_snapshots == 1
        assert stats.total_size_bytes == len("content")

    def test_rollback_count_in_stats(self, cp, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("original")
        cp.snapshot(str(f))
        f.write_text("changed")
        cp.rollback(str(f))
        assert cp.stats().rollbacks_performed == 1


# ── Cleanup ──────────────────────────────────────────────────────

class TestCleanup:

    def test_cleanup(self, cp, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("x")
        cp.snapshot(str(f))
        count = cp.cleanup()
        assert count >= 1
        assert cp.snapshot_count() == 0
