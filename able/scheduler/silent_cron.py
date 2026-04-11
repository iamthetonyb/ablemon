"""
F9 — Silent Cron Responses.

Adds `[SILENT]` response capability to cron jobs. When a cron agent's
output starts with `[SILENT]`, the completion is recorded but NOT
delivered to the user's chat. Reduces noise from background jobs
that have nothing interesting to report.

Usage:
    from able.scheduler.silent_cron import SilentFilter, SilentStats

    filt = SilentFilter()

    # Check if output should be suppressed
    result = filt.check("[SILENT] Nothing new")
    assert result.silent is True
    assert result.reason == "explicit_marker"

    # Regular output passes through
    result = filt.check("Found 3 new insights")
    assert result.silent is False

    # Empty/trivial output also suppressed
    result = filt.check("")
    assert result.silent is True
    assert result.reason == "empty"
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Pattern for the [SILENT] marker
_SILENT_PATTERN = re.compile(r"^\s*\[SILENT\]", re.IGNORECASE)

# Trivial outputs that aren't worth delivering
_TRIVIAL_PATTERNS = [
    re.compile(r"^\s*ok\s*$", re.IGNORECASE),
    re.compile(r"^\s*done\.?\s*$", re.IGNORECASE),
    re.compile(r"^\s*no\s+(changes?|updates?|results?)\s*\.?\s*$", re.IGNORECASE),
    re.compile(r"^\s*nothing\s+to\s+(report|do|process)\s*\.?\s*$", re.IGNORECASE),
]


@dataclass
class SilentCheckResult:
    """Result of checking whether output should be silent."""
    silent: bool
    reason: str = ""  # "explicit_marker", "empty", "trivial", ""
    original_output: str = ""
    stripped_output: str = ""  # Output with [SILENT] marker removed


@dataclass
class SilentStats:
    """Aggregate silent filtering statistics."""
    total_checked: int = 0
    suppressed: int = 0
    delivered: int = 0
    by_reason: Dict[str, int] = field(default_factory=lambda: {
        "explicit_marker": 0,
        "empty": 0,
        "trivial": 0,
    })

    @property
    def suppression_rate(self) -> float:
        if self.total_checked == 0:
            return 0.0
        return self.suppressed / self.total_checked


class SilentFilter:
    """Filters cron job outputs to suppress noisy or empty responses.

    Three suppression modes:
    1. Explicit: Output starts with [SILENT]
    2. Empty: Output is empty or whitespace-only
    3. Trivial: Output matches known no-op patterns (configurable)

    Non-silent outputs pass through unchanged.
    """

    def __init__(self, suppress_trivial: bool = True):
        """
        Args:
            suppress_trivial: If True, also suppress trivial outputs
                like "ok", "done", "no changes".
        """
        self._suppress_trivial = suppress_trivial
        self._stats = SilentStats()

    def check(self, output: Optional[str]) -> SilentCheckResult:
        """Check whether a cron output should be suppressed.

        Args:
            output: The cron job's output string.

        Returns:
            SilentCheckResult with silent flag and reason.
        """
        self._stats.total_checked += 1

        # Empty / None
        if not output or not output.strip():
            self._stats.suppressed += 1
            self._stats.by_reason["empty"] += 1
            return SilentCheckResult(
                silent=True,
                reason="empty",
                original_output=output or "",
                stripped_output="",
            )

        # Explicit [SILENT] marker
        if _SILENT_PATTERN.match(output):
            stripped = _SILENT_PATTERN.sub("", output).strip()
            self._stats.suppressed += 1
            self._stats.by_reason["explicit_marker"] += 1
            logger.debug("Silent cron output suppressed: %s", output[:80])
            return SilentCheckResult(
                silent=True,
                reason="explicit_marker",
                original_output=output,
                stripped_output=stripped,
            )

        # Trivial patterns
        if self._suppress_trivial:
            for pattern in _TRIVIAL_PATTERNS:
                if pattern.match(output):
                    self._stats.suppressed += 1
                    self._stats.by_reason["trivial"] += 1
                    return SilentCheckResult(
                        silent=True,
                        reason="trivial",
                        original_output=output,
                        stripped_output=output.strip(),
                    )

        # Not silent — deliver
        self._stats.delivered += 1
        return SilentCheckResult(
            silent=False,
            reason="",
            original_output=output,
            stripped_output=output.strip(),
        )

    @property
    def stats(self) -> SilentStats:
        return self._stats

    def reset_stats(self) -> None:
        self._stats = SilentStats()
