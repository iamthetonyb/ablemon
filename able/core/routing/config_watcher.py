"""
E6 — Real-Time Config Reload.

Polling-based file watcher for YAML config files. Detects changes via
MD5 hash comparison and triggers reload callbacks. No external dependencies
(no watchdog/inotify required).

Supports ${ENV_VAR} and ${VAR:-default} substitution via the existing
ProviderRegistry._substitute_env_vars() mechanism.

Usage:
    watcher = ConfigWatcher()
    watcher.watch(
        "config/routing_config.yaml",
        on_change=lambda path: registry.reload_from_yaml(path),
    )
    watcher.start()  # Background polling
    ...
    watcher.stop()

Integration:
    Wire into gateway.py __init__() — register watched configs, start
    watcher. Wire into gateway shutdown — stop watcher.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class WatchedFile:
    """A config file being watched for changes."""

    path: str
    on_change: Callable[[str], Any]
    last_hash: str = ""
    last_modified: float = 0.0
    last_checked: float = 0.0
    reload_count: int = 0
    last_error: Optional[str] = None


@dataclass
class WatcherStats:
    """Watcher performance statistics."""

    checks: int = 0
    reloads: int = 0
    errors: int = 0
    files_watched: int = 0
    uptime_s: float = 0.0

    def summary(self) -> str:
        return (
            f"files={self.files_watched} checks={self.checks} "
            f"reloads={self.reloads} errors={self.errors} "
            f"uptime={self.uptime_s:.0f}s"
        )


# Max file size to hash (10 MB) — prevents OOM on unexpected large files
_MAX_HASH_FILE_SIZE = 10 * 1024 * 1024


def _file_hash(path: str) -> str:
    """MD5 hash of file contents. Skips files > 10 MB."""
    try:
        p = Path(path)
        if p.stat().st_size > _MAX_HASH_FILE_SIZE:
            logger.warning("Config file too large to hash: %s", path)
            return ""
        data = p.read_bytes()
        return hashlib.md5(data).hexdigest()
    except (OSError, IOError):
        return ""


def substitute_env_vars(text: str) -> str:
    """Replace ${ENV_VAR} and ${VAR:-default} in text with env values.

    Matches the pattern from ProviderRegistry._substitute_env_vars().
    """
    pattern = r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}"

    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        default = match.group(2)
        value = os.environ.get(var_name)
        if value is not None:
            return value
        if default is not None:
            return default
        return ""

    return re.sub(pattern, replacer, text)


class ConfigWatcher:
    """Polling-based config file watcher.

    Checks watched files at a configurable interval. When a file's hash
    changes, calls its registered on_change callback.

    Thread-safe: runs in a daemon thread, callbacks execute in that thread.
    """

    def __init__(
        self,
        poll_interval_s: float = 5.0,
        max_errors_before_skip: int = 5,
    ):
        self._files: Dict[str, WatchedFile] = {}
        self._poll_interval = poll_interval_s
        self._max_errors = max_errors_before_skip
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._started_at: Optional[float] = None
        self._stats = WatcherStats()
        self._consecutive_errors: Dict[str, int] = {}

    def watch(
        self,
        path: str,
        on_change: Callable[[str], Any],
    ) -> None:
        """Register a file to watch.

        Args:
            path: Path to the config file.
            on_change: Callback invoked with the file path when content changes.
        """
        abs_path = str(Path(path).resolve())
        current_hash = _file_hash(abs_path)

        with self._lock:
            self._files[abs_path] = WatchedFile(
                path=abs_path,
                on_change=on_change,
                last_hash=current_hash,
                last_modified=_get_mtime(abs_path),
                last_checked=time.time(),
            )
            self._stats.files_watched = len(self._files)

    def unwatch(self, path: str) -> bool:
        """Stop watching a file."""
        abs_path = str(Path(path).resolve())
        with self._lock:
            removed = self._files.pop(abs_path, None) is not None
            self._stats.files_watched = len(self._files)
            return removed

    def start(self) -> None:
        """Start the background polling thread."""
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._started_at = time.time()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="config-watcher",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Config watcher started: %d files, interval=%.1fs",
            len(self._files), self._poll_interval,
        )

    def stop(self) -> None:
        """Stop the background polling thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self._poll_interval + 1)
            self._thread = None
        logger.info("Config watcher stopped: %s", self._stats.summary())

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def check_now(self) -> List[str]:
        """Manually trigger a check cycle. Returns list of changed file paths."""
        return self._check_all()

    def _poll_loop(self) -> None:
        """Background polling loop."""
        while not self._stop_event.is_set():
            self._check_all()
            self._stop_event.wait(self._poll_interval)

    def _check_all(self) -> List[str]:
        """Check all watched files for changes."""
        changed = []
        with self._lock:
            files = list(self._files.values())

        for wf in files:
            self._stats.checks += 1
            wf.last_checked = time.time()

            # Skip if too many consecutive errors
            if self._consecutive_errors.get(wf.path, 0) >= self._max_errors:
                continue

            # Quick mtime check before expensive hash
            current_mtime = _get_mtime(wf.path)
            if current_mtime == wf.last_modified and wf.last_hash:
                continue

            current_hash = _file_hash(wf.path)
            if not current_hash:
                self._consecutive_errors[wf.path] = (
                    self._consecutive_errors.get(wf.path, 0) + 1
                )
                wf.last_error = "File not readable"
                self._stats.errors += 1
                continue

            if current_hash != wf.last_hash:
                try:
                    wf.on_change(wf.path)
                    wf.last_hash = current_hash
                    wf.last_modified = current_mtime
                    wf.reload_count += 1
                    wf.last_error = None
                    self._consecutive_errors[wf.path] = 0
                    self._stats.reloads += 1
                    changed.append(wf.path)
                    logger.info("Config reloaded: %s (reload #%d)", wf.path, wf.reload_count)
                except Exception as e:
                    wf.last_error = str(e)
                    self._consecutive_errors[wf.path] = (
                        self._consecutive_errors.get(wf.path, 0) + 1
                    )
                    self._stats.errors += 1
                    logger.warning("Config reload failed: %s — %s", wf.path, e)
            else:
                wf.last_modified = current_mtime
                self._consecutive_errors[wf.path] = 0

        if self._started_at:
            self._stats.uptime_s = time.time() - self._started_at

        return changed

    @property
    def stats(self) -> WatcherStats:
        if self._started_at:
            self._stats.uptime_s = time.time() - self._started_at
        return self._stats

    @property
    def watched_files(self) -> List[str]:
        with self._lock:
            return list(self._files.keys())

    def file_stats(self, path: str) -> Optional[Dict]:
        """Get stats for a specific watched file."""
        abs_path = str(Path(path).resolve())
        with self._lock:
            wf = self._files.get(abs_path)
            if not wf:
                return None
            return {
                "path": wf.path,
                "reload_count": wf.reload_count,
                "last_checked": wf.last_checked,
                "last_error": wf.last_error,
                "hash": wf.last_hash[:8] if wf.last_hash else "",
            }


def _get_mtime(path: str) -> float:
    """Get file modification time, 0.0 if not found."""
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0
