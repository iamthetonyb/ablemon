"""Tests for E6 — Real-Time Config Reload.

Covers: file watching, change detection, callback firing, mtime + hash,
env var substitution, error handling, stats, start/stop lifecycle.
"""

import os
import tempfile
import time
import pytest

from able.core.routing.config_watcher import (
    ConfigWatcher,
    WatcherStats,
    substitute_env_vars,
    _file_hash,
)


@pytest.fixture
def tmp_config():
    """Create a temporary config file."""
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False
    )
    f.write("key: value1\n")
    f.flush()
    f.close()
    yield f.name
    try:
        os.unlink(f.name)
    except OSError:
        pass


@pytest.fixture
def watcher():
    w = ConfigWatcher(poll_interval_s=0.1)
    yield w
    if w.is_running:
        w.stop()


# ── Env var substitution ───────────────────────────────────────

class TestEnvVarSubstitution:

    def test_simple_var(self, monkeypatch):
        monkeypatch.setenv("MY_VAR", "hello")
        assert substitute_env_vars("val=${MY_VAR}") == "val=hello"

    def test_default_value(self):
        # Ensure var is NOT set
        os.environ.pop("MISSING_VAR_XYZ", None)
        assert substitute_env_vars("${MISSING_VAR_XYZ:-fallback}") == "fallback"

    def test_missing_no_default(self):
        os.environ.pop("MISSING_VAR_ABC", None)
        assert substitute_env_vars("${MISSING_VAR_ABC}") == ""

    def test_multiple_vars(self, monkeypatch):
        monkeypatch.setenv("A", "1")
        monkeypatch.setenv("B", "2")
        result = substitute_env_vars("${A}-${B}")
        assert result == "1-2"

    def test_no_vars(self):
        assert substitute_env_vars("plain text") == "plain text"


# ── File hash ──────────────────────────────────────────────────

class TestFileHash:

    def test_hash_deterministic(self, tmp_config):
        h1 = _file_hash(tmp_config)
        h2 = _file_hash(tmp_config)
        assert h1 == h2
        assert len(h1) == 32  # MD5 hex

    def test_hash_missing_file(self):
        assert _file_hash("/nonexistent/file.yaml") == ""


# ── Watch / Unwatch ────────────────────────────────────────────

class TestWatchUnwatch:

    def test_watch_file(self, watcher, tmp_config):
        watcher.watch(tmp_config, on_change=lambda p: None)
        assert len(watcher.watched_files) == 1

    def test_unwatch_file(self, watcher, tmp_config):
        watcher.watch(tmp_config, on_change=lambda p: None)
        assert watcher.unwatch(tmp_config) is True
        assert len(watcher.watched_files) == 0

    def test_unwatch_nonexistent(self, watcher):
        assert watcher.unwatch("/ghost.yaml") is False


# ── Change detection ───────────────────────────────────────────

class TestChangeDetection:

    def test_detect_change(self, watcher, tmp_config):
        changes = []
        watcher.watch(tmp_config, on_change=lambda p: changes.append(p))

        # No change yet
        result = watcher.check_now()
        assert len(result) == 0

        # Modify file
        time.sleep(0.01)  # Ensure mtime changes
        with open(tmp_config, "w") as f:
            f.write("key: value2\n")

        result = watcher.check_now()
        assert len(result) == 1
        assert len(changes) == 1

    def test_no_change(self, watcher, tmp_config):
        changes = []
        watcher.watch(tmp_config, on_change=lambda p: changes.append(p))
        watcher.check_now()
        watcher.check_now()
        assert len(changes) == 0

    def test_callback_error_handled(self, watcher, tmp_config):
        def bad_callback(p):
            raise RuntimeError("reload failed")

        watcher.watch(tmp_config, on_change=bad_callback)

        # Modify file
        time.sleep(0.01)
        with open(tmp_config, "w") as f:
            f.write("key: broken\n")

        # Should not raise
        watcher.check_now()
        assert watcher.stats.errors == 1


# ── Background polling ─────────────────────────────────────────

class TestPolling:

    def test_start_stop(self, watcher, tmp_config):
        watcher.watch(tmp_config, on_change=lambda p: None)
        watcher.start()
        assert watcher.is_running
        watcher.stop()
        assert not watcher.is_running

    def test_start_idempotent(self, watcher, tmp_config):
        watcher.watch(tmp_config, on_change=lambda p: None)
        watcher.start()
        watcher.start()  # Should not create second thread
        assert watcher.is_running
        watcher.stop()

    def test_background_detects_change(self, watcher, tmp_config):
        changes = []
        watcher.watch(tmp_config, on_change=lambda p: changes.append(p))
        watcher.start()

        # Modify file
        time.sleep(0.05)
        with open(tmp_config, "w") as f:
            f.write("key: background_change\n")

        # Wait for poll to detect
        time.sleep(0.3)
        watcher.stop()
        assert len(changes) >= 1


# ── Stats ──────────────────────────────────────────────────────

class TestStats:

    def test_initial_stats(self, watcher):
        s = watcher.stats
        assert isinstance(s, WatcherStats)
        assert s.checks == 0
        assert s.reloads == 0

    def test_stats_after_check(self, watcher, tmp_config):
        watcher.watch(tmp_config, on_change=lambda p: None)
        watcher.check_now()
        assert watcher.stats.checks >= 1

    def test_file_stats(self, watcher, tmp_config):
        watcher.watch(tmp_config, on_change=lambda p: None)
        fs = watcher.file_stats(tmp_config)
        assert fs is not None
        assert "path" in fs
        assert "reload_count" in fs

    def test_file_stats_missing(self, watcher):
        assert watcher.file_stats("/ghost.yaml") is None

    def test_summary(self, watcher):
        s = watcher.stats.summary()
        assert "files=" in s
        assert "reloads=" in s
