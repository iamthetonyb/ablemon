"""
F6 — Gateway Hardening.

Startup bounding (max init time before serving health), disconnect
abort (clean shutdown on persistent disconnects), graceful shutdown
sequence.

Forked from OpenClaw v4.9 gateway hardening pattern.

Usage:
    harness = GatewayHarness(max_startup_s=30)
    harness.begin_startup()
    # ... initialize providers, load configs ...
    harness.startup_complete()

    # During operation:
    harness.record_disconnect()
    if harness.should_abort():
        await harness.graceful_shutdown()
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ShutdownPhase:
    """A named phase in the graceful shutdown sequence."""
    name: str
    fn: Callable[..., Coroutine]
    timeout_s: float = 10.0
    critical: bool = False  # If True, abort if this phase fails


@dataclass
class HarnessStats:
    """Stats from the gateway harness."""
    startup_duration_ms: float = 0
    startup_timed_out: bool = False
    total_disconnects: int = 0
    consecutive_disconnects: int = 0
    abort_triggered: bool = False
    shutdown_complete: bool = False
    uptime_s: float = 0


class GatewayHarness:
    """Hardening harness for the ABLE gateway.

    Provides:
    - Startup bounding: max time before the gateway must be serving
    - Disconnect tracking: detects persistent connection failures
    - Graceful shutdown: ordered phase execution with timeouts
    """

    def __init__(
        self,
        max_startup_s: float = 30.0,
        max_consecutive_disconnects: int = 5,
        disconnect_window_s: float = 60.0,
    ):
        """
        Args:
            max_startup_s: Maximum time allowed for startup before health fails.
            max_consecutive_disconnects: Consecutive disconnects before abort.
            disconnect_window_s: Window for counting disconnect rate.
        """
        self._max_startup_s = max_startup_s
        self._max_disconnects = max_consecutive_disconnects
        self._disconnect_window = disconnect_window_s

        self._startup_start: Optional[float] = None
        self._startup_end: Optional[float] = None
        self._startup_timed_out = False

        self._disconnects: List[float] = []  # timestamps
        self._consecutive_disconnects = 0
        self._abort_triggered = False
        self._shutdown_complete = False

        self._shutdown_phases: List[ShutdownPhase] = []
        self._on_abort: Optional[Callable] = None

    def begin_startup(self) -> None:
        """Mark the start of the startup sequence."""
        self._startup_start = time.monotonic()
        logger.info("Gateway startup begun (max %.1fs)", self._max_startup_s)

    def startup_complete(self) -> None:
        """Mark startup as successfully complete."""
        self._startup_end = time.monotonic()
        if self._startup_start:
            duration = (self._startup_end - self._startup_start) * 1000
            logger.info("Gateway startup complete in %.0fms", duration)

    def is_startup_expired(self) -> bool:
        """Check if startup has exceeded the time limit."""
        if self._startup_end:
            return False  # Already complete
        if not self._startup_start:
            return False  # Not started
        elapsed = time.monotonic() - self._startup_start
        if elapsed > self._max_startup_s:
            self._startup_timed_out = True
            return True
        return False

    def is_healthy(self) -> bool:
        """Check if the gateway is in a healthy state.

        Returns False if startup timed out or abort was triggered.
        """
        if self._abort_triggered:
            return False
        if self.is_startup_expired():
            return False
        return True

    # ── Disconnect tracking ──────────────────────────────────────

    def record_disconnect(self) -> None:
        """Record a provider disconnect event."""
        now = time.monotonic()
        self._disconnects.append(now)
        self._consecutive_disconnects += 1

        # Prune old disconnects outside the window
        cutoff = now - self._disconnect_window
        self._disconnects = [t for t in self._disconnects if t > cutoff]

        logger.warning(
            "Disconnect recorded: %d consecutive, %d in window",
            self._consecutive_disconnects,
            len(self._disconnects),
        )

    def record_success(self) -> None:
        """Record a successful operation, resetting consecutive disconnect count."""
        self._consecutive_disconnects = 0

    def should_abort(self) -> bool:
        """Check if persistent disconnects warrant an abort."""
        if self._consecutive_disconnects >= self._max_disconnects:
            self._abort_triggered = True
            logger.error(
                "Abort triggered: %d consecutive disconnects (max %d)",
                self._consecutive_disconnects,
                self._max_disconnects,
            )
            return True
        return False

    # ── Graceful shutdown ────────────────────────────────────────

    def register_shutdown_phase(
        self,
        name: str,
        fn: Callable[..., Coroutine],
        timeout_s: float = 10.0,
        critical: bool = False,
    ) -> None:
        """Register a shutdown phase.

        Phases execute in registration order during graceful shutdown.

        Args:
            name: Phase name for logging.
            fn: Async callable to execute.
            timeout_s: Timeout for this phase.
            critical: If True, abort shutdown if this phase fails.
        """
        self._shutdown_phases.append(ShutdownPhase(
            name=name, fn=fn, timeout_s=timeout_s, critical=critical,
        ))

    async def graceful_shutdown(self) -> Dict[str, bool]:
        """Execute graceful shutdown sequence.

        Runs registered phases in order. Each phase has its own timeout.
        Returns dict of {phase_name: success}.
        """
        results = {}
        logger.info("Graceful shutdown initiated (%d phases)", len(self._shutdown_phases))

        for phase in self._shutdown_phases:
            try:
                await asyncio.wait_for(phase.fn(), timeout=phase.timeout_s)
                results[phase.name] = True
                logger.info("Shutdown phase '%s' complete", phase.name)
            except asyncio.TimeoutError:
                results[phase.name] = False
                logger.error(
                    "Shutdown phase '%s' timed out (%.1fs)",
                    phase.name, phase.timeout_s,
                )
                if phase.critical:
                    logger.error("Critical phase failed — aborting shutdown")
                    break
            except Exception as e:
                results[phase.name] = False
                logger.error("Shutdown phase '%s' failed: %s", phase.name, e)
                if phase.critical:
                    break

        self._shutdown_complete = True
        return results

    def stats(self) -> HarnessStats:
        """Return harness stats."""
        startup_ms = 0
        if self._startup_start and self._startup_end:
            startup_ms = (self._startup_end - self._startup_start) * 1000
        uptime = 0
        if self._startup_end:
            uptime = time.monotonic() - self._startup_end

        return HarnessStats(
            startup_duration_ms=startup_ms,
            startup_timed_out=self._startup_timed_out,
            total_disconnects=len(self._disconnects),
            consecutive_disconnects=self._consecutive_disconnects,
            abort_triggered=self._abort_triggered,
            shutdown_complete=self._shutdown_complete,
            uptime_s=uptime,
        )
