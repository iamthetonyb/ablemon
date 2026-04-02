"""
Sliding Window Rate Limiter

Time-windowed rate limiting with smooth rollover.
"""

import time
import threading
from collections import deque
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class SlidingWindowState:
    """Serializable state for persistence"""
    events: List[Tuple[float, int]]  # (timestamp, count) pairs
    limit: int
    window_seconds: float


class SlidingWindow:
    """
    Sliding window rate limiter.

    Tracks events within a time window and limits based on count.
    Uses sub-windows for memory efficiency.

    Example:
        window = SlidingWindow(limit=100, window_seconds=3600)  # 100/hour
        if window.check(1):
            # Operation allowed
            window.record(1)
        else:
            # Rate limited
    """

    def __init__(
        self,
        limit: int,
        window_seconds: float,
        sub_window_count: int = 60
    ):
        """
        Initialize sliding window.

        Args:
            limit: Maximum count within window
            window_seconds: Window size in seconds
            sub_window_count: Number of sub-windows (more = smoother but more memory)
        """
        self.limit = limit
        self.window_seconds = window_seconds
        self.sub_window_seconds = window_seconds / sub_window_count

        # Store (timestamp, count) for each sub-window
        self.events: deque = deque()
        self._lock = threading.Lock()

    def check(self, count: int = 1) -> bool:
        """
        Check if count would exceed limit.

        Does NOT record the event.

        Args:
            count: Amount to check

        Returns:
            True if allowed, False if would exceed limit
        """
        with self._lock:
            self._cleanup()
            current = self._current_count()
            return (current + count) <= self.limit

    def record(self, count: int = 1) -> bool:
        """
        Record an event.

        Args:
            count: Amount to record

        Returns:
            True if recorded successfully
        """
        with self._lock:
            self._cleanup()
            now = time.time()

            # Find or create current sub-window
            if self.events and self._same_subwindow(self.events[-1][0], now):
                # Update existing sub-window
                last_ts, last_count = self.events.pop()
                self.events.append((last_ts, last_count + count))
            else:
                # New sub-window
                self.events.append((now, count))

            return True

    def check_and_record(self, count: int = 1) -> bool:
        """
        Atomically check and record if allowed.

        Args:
            count: Amount to check and record

        Returns:
            True if recorded, False if would exceed limit
        """
        with self._lock:
            self._cleanup()
            current = self._current_count()

            if (current + count) <= self.limit:
                now = time.time()
                if self.events and self._same_subwindow(self.events[-1][0], now):
                    last_ts, last_count = self.events.pop()
                    self.events.append((last_ts, last_count + count))
                else:
                    self.events.append((now, count))
                return True

            return False

    def _same_subwindow(self, ts1: float, ts2: float) -> bool:
        """Check if two timestamps are in the same sub-window"""
        return int(ts1 / self.sub_window_seconds) == int(ts2 / self.sub_window_seconds)

    def _cleanup(self):
        """Remove expired sub-windows"""
        cutoff = time.time() - self.window_seconds
        while self.events and self.events[0][0] < cutoff:
            self.events.popleft()

    def _current_count(self) -> int:
        """Get current count within window"""
        return sum(count for _, count in self.events)

    def get_count(self) -> int:
        """Get current count (thread-safe)"""
        with self._lock:
            self._cleanup()
            return self._current_count()

    def get_remaining(self) -> int:
        """Get remaining capacity"""
        with self._lock:
            self._cleanup()
            return max(0, self.limit - self._current_count())

    def time_until_capacity(self, needed: int = 1) -> float:
        """
        Calculate seconds until specified capacity available.

        Args:
            needed: Capacity needed

        Returns:
            Seconds to wait, 0 if already available
        """
        with self._lock:
            self._cleanup()
            current = self._current_count()
            available = self.limit - current

            if available >= needed:
                return 0.0

            # Find oldest events to expire
            need_to_expire = needed - available
            expired = 0
            oldest_needed = time.time()

            for ts, count in self.events:
                expired += count
                if expired >= need_to_expire:
                    oldest_needed = ts
                    break

            # Time until that event expires
            return max(0, (oldest_needed + self.window_seconds) - time.time())

    def get_state(self) -> SlidingWindowState:
        """Get serializable state"""
        with self._lock:
            self._cleanup()
            return SlidingWindowState(
                events=list(self.events),
                limit=self.limit,
                window_seconds=self.window_seconds
            )

    @classmethod
    def from_state(cls, state: SlidingWindowState) -> 'SlidingWindow':
        """Restore from serialized state"""
        window = cls(
            limit=state.limit,
            window_seconds=state.window_seconds
        )
        window.events = deque(state.events)
        return window

    def reset(self):
        """Clear all recorded events"""
        with self._lock:
            self.events.clear()

    def __repr__(self) -> str:
        count = self.get_count()
        return f"SlidingWindow({count}/{self.limit} in {self.window_seconds}s)"
