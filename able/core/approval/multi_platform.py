"""
F3 — Multi-Platform Approval Routing.

Routes approval requests to whichever channel the operator is active on.
Supports fallback chains and escalation timeouts. If no channel responds
within the timeout, the request is denied by default.

Usage:
    router = ApprovalRouter()
    router.register_channel("telegram", telegram_adapter, priority=1)
    router.register_channel("discord", discord_adapter, priority=2)
    router.register_channel("cli", cli_adapter, priority=3)

    result = await router.request_approval(
        action="rm -rf /tmp/build",
        context={"risk": "medium"},
        timeout_s=60,
    )
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable, Dict, List, Optional

logger = logging.getLogger(__name__)


class ApprovalDecision(str, Enum):
    APPROVED = "approved"
    DENIED = "denied"
    TIMEOUT = "timeout"
    ESCALATED = "escalated"


@dataclass
class ApprovalRequest:
    """An approval request to be routed."""
    id: str
    action: str
    context: Dict[str, Any] = field(default_factory=dict)
    risk_level: str = "medium"
    created_at: float = field(default_factory=time.time)


@dataclass
class ApprovalResponse:
    """Response from an approval channel."""
    request_id: str
    decision: ApprovalDecision
    channel: str = ""
    responder: str = ""
    reason: str = ""
    response_time_s: float = 0.0


@dataclass
class ChannelRegistration:
    """A registered approval channel."""
    name: str
    send_fn: Callable[[ApprovalRequest], Awaitable[Optional[ApprovalResponse]]]
    priority: int = 10  # Lower = tried first
    is_active: bool = True
    last_response_at: float = 0.0


@dataclass
class RouterStats:
    """Approval routing statistics."""
    total_requests: int = 0
    approved: int = 0
    denied: int = 0
    timed_out: int = 0
    escalated: int = 0
    by_channel: Dict[str, int] = field(default_factory=dict)


class ApprovalRouter:
    """Multi-platform approval routing with fallback and escalation.

    Channels are tried in priority order. If a channel doesn't respond
    within its portion of the timeout, the next channel is tried.
    If all channels fail, the request times out (denied by default).
    """

    def __init__(self, default_timeout_s: float = 120.0):
        self._channels: Dict[str, ChannelRegistration] = {}
        self._default_timeout = default_timeout_s
        self._stats = RouterStats()
        self._pending: Dict[str, ApprovalRequest] = {}

    def register_channel(
        self,
        name: str,
        send_fn: Callable[[ApprovalRequest], Awaitable[Optional[ApprovalResponse]]],
        priority: int = 10,
    ) -> None:
        """Register an approval channel.

        Args:
            name: Channel identifier (e.g., "telegram", "discord", "cli").
            send_fn: Async function that sends the approval request and
                     returns a response (or None if no response).
            priority: Lower number = tried first.
        """
        self._channels[name] = ChannelRegistration(
            name=name,
            send_fn=send_fn,
            priority=priority,
        )

    def unregister_channel(self, name: str) -> None:
        """Remove a channel."""
        self._channels.pop(name, None)

    def set_channel_active(self, name: str, active: bool) -> None:
        """Mark a channel as active/inactive."""
        if name in self._channels:
            self._channels[name].is_active = active

    async def request_approval(
        self,
        request: ApprovalRequest,
        timeout_s: Optional[float] = None,
    ) -> ApprovalResponse:
        """Route an approval request through available channels.

        Tries channels in priority order. Each channel gets a proportional
        share of the total timeout.

        Args:
            request: The approval request.
            timeout_s: Total timeout in seconds.

        Returns:
            ApprovalResponse with the decision.
        """
        self._stats.total_requests += 1
        self._pending[request.id] = request
        total_timeout = timeout_s or self._default_timeout

        try:
            return await self._route_through_channels(request, total_timeout)
        finally:
            # Always clean up pending, even on unexpected errors
            self._pending.pop(request.id, None)

    async def _route_through_channels(
        self,
        request: ApprovalRequest,
        total_timeout: float,
    ) -> ApprovalResponse:
        """Internal: try each channel in priority order."""
        # Sort channels by priority
        active_channels = sorted(
            [c for c in self._channels.values() if c.is_active],
            key=lambda c: c.priority,
        )

        if not active_channels:
            self._stats.timed_out += 1
            return ApprovalResponse(
                request_id=request.id,
                decision=ApprovalDecision.TIMEOUT,
                reason="No active approval channels",
            )

        # Divide timeout among channels
        per_channel_timeout = total_timeout / len(active_channels)

        for channel in active_channels:
            try:
                response = await asyncio.wait_for(
                    channel.send_fn(request),
                    timeout=per_channel_timeout,
                )

                if response is not None:
                    response.channel = channel.name
                    response.response_time_s = time.time() - request.created_at
                    channel.last_response_at = time.time()

                    # Track stats
                    self._stats.by_channel[channel.name] = (
                        self._stats.by_channel.get(channel.name, 0) + 1
                    )
                    if response.decision == ApprovalDecision.APPROVED:
                        self._stats.approved += 1
                    elif response.decision == ApprovalDecision.DENIED:
                        self._stats.denied += 1

                    return response

            except asyncio.TimeoutError:
                logger.debug(
                    "Channel '%s' timed out for request %s",
                    channel.name, request.id,
                )
                continue
            except Exception as e:
                logger.warning(
                    "Channel '%s' error for request %s: %s",
                    channel.name, request.id, e,
                )
                continue

        # All channels failed
        self._stats.timed_out += 1
        return ApprovalResponse(
            request_id=request.id,
            decision=ApprovalDecision.TIMEOUT,
            reason="All channels timed out",
            response_time_s=total_timeout,
        )

    @property
    def stats(self) -> RouterStats:
        return self._stats

    @property
    def active_channels(self) -> List[str]:
        return [
            c.name for c in self._channels.values()
            if c.is_active
        ]

    @property
    def pending_count(self) -> int:
        return len(self._pending)
