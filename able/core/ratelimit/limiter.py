"""
Unified Rate Limiter

Combines token bucket and sliding window for multi-tier rate limiting.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta

from .token_bucket import TokenBucket
from .sliding_window import SlidingWindow

logger = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    """Raised when rate limit is exceeded"""
    def __init__(self, limit_type: str, retry_after: float = 0):
        self.limit_type = limit_type
        self.retry_after = retry_after
        super().__init__(f"Rate limit exceeded: {limit_type}, retry after {retry_after:.1f}s")


@dataclass
class RateLimitResult:
    """Result of a rate limit check"""
    allowed: bool
    limit_type: Optional[str] = None  # Which limit was hit
    current: int = 0
    limit: int = 0
    retry_after: float = 0.0
    remaining: int = 0

    def raise_if_limited(self):
        """Raise exception if rate limited"""
        if not self.allowed:
            raise RateLimitExceeded(self.limit_type or "unknown", self.retry_after)


@dataclass
class ClientLimits:
    """Rate limits for a client"""
    messages_per_minute: int = 20
    messages_per_hour: int = 200
    tokens_per_day: int = 100000
    commands_per_hour: int = 50


class RateLimiter:
    """
    Multi-tier rate limiter for ABLE clients.

    Implements:
    - Message rate limiting (per minute, per hour)
    - Token usage limiting (per day)
    - Command rate limiting (per hour)

    Uses token buckets for burst allowance and sliding windows for
    sustained rate limiting.
    """

    def __init__(self):
        # Per-client limiters
        self.message_buckets: Dict[str, TokenBucket] = {}
        self.message_windows: Dict[str, SlidingWindow] = {}
        self.token_windows: Dict[str, SlidingWindow] = {}
        self.command_windows: Dict[str, SlidingWindow] = {}

        # Client configurations
        self.client_limits: Dict[str, ClientLimits] = {}

        # Default limits
        self.default_limits = ClientLimits()

    def set_client_limits(self, client_id: str, limits: ClientLimits):
        """Configure limits for a specific client"""
        self.client_limits[client_id] = limits

    def get_limits(self, client_id: str) -> ClientLimits:
        """Get limits for a client"""
        return self.client_limits.get(client_id, self.default_limits)

    def _get_message_bucket(self, client_id: str) -> TokenBucket:
        """Get or create message token bucket for client"""
        if client_id not in self.message_buckets:
            limits = self.get_limits(client_id)
            # Token bucket for burst: allow full minute's worth, refill per second
            self.message_buckets[client_id] = TokenBucket(
                capacity=limits.messages_per_minute,
                refill_rate=limits.messages_per_minute / 60.0
            )
        return self.message_buckets[client_id]

    def _get_message_window(self, client_id: str) -> SlidingWindow:
        """Get or create hourly message window for client"""
        if client_id not in self.message_windows:
            limits = self.get_limits(client_id)
            self.message_windows[client_id] = SlidingWindow(
                limit=limits.messages_per_hour,
                window_seconds=3600
            )
        return self.message_windows[client_id]

    def _get_token_window(self, client_id: str) -> SlidingWindow:
        """Get or create daily token window for client"""
        if client_id not in self.token_windows:
            limits = self.get_limits(client_id)
            self.token_windows[client_id] = SlidingWindow(
                limit=limits.tokens_per_day,
                window_seconds=86400
            )
        return self.token_windows[client_id]

    def _get_command_window(self, client_id: str) -> SlidingWindow:
        """Get or create hourly command window for client"""
        if client_id not in self.command_windows:
            limits = self.get_limits(client_id)
            self.command_windows[client_id] = SlidingWindow(
                limit=limits.commands_per_hour,
                window_seconds=3600
            )
        return self.command_windows[client_id]

    async def check_message_limit(
        self,
        client_id: str,
        count: int = 1
    ) -> RateLimitResult:
        """
        Check if a message can be sent.

        Checks both burst (token bucket) and sustained (sliding window) limits.
        """
        limits = self.get_limits(client_id)

        # Check burst limit (token bucket)
        bucket = self._get_message_bucket(client_id)
        if not bucket.consume(count):
            return RateLimitResult(
                allowed=False,
                limit_type="messages_per_minute",
                current=limits.messages_per_minute - int(bucket.get_tokens()),
                limit=limits.messages_per_minute,
                retry_after=bucket.time_until_tokens(count),
                remaining=int(bucket.get_tokens())
            )

        # Check hourly limit (sliding window)
        window = self._get_message_window(client_id)
        if not window.check_and_record(count):
            # Undo bucket consumption
            bucket.tokens += count
            return RateLimitResult(
                allowed=False,
                limit_type="messages_per_hour",
                current=window.get_count(),
                limit=limits.messages_per_hour,
                retry_after=window.time_until_capacity(count),
                remaining=window.get_remaining()
            )

        return RateLimitResult(
            allowed=True,
            remaining=min(int(bucket.get_tokens()), window.get_remaining())
        )

    async def check_token_limit(
        self,
        client_id: str,
        tokens: int
    ) -> RateLimitResult:
        """Check if token usage is within daily limit"""
        limits = self.get_limits(client_id)
        window = self._get_token_window(client_id)

        if not window.check(tokens):
            return RateLimitResult(
                allowed=False,
                limit_type="tokens_per_day",
                current=window.get_count(),
                limit=limits.tokens_per_day,
                retry_after=window.time_until_capacity(tokens),
                remaining=window.get_remaining()
            )

        return RateLimitResult(
            allowed=True,
            remaining=window.get_remaining()
        )

    async def record_token_usage(
        self,
        client_id: str,
        tokens: int
    ):
        """Record token usage (call after successful completion)"""
        window = self._get_token_window(client_id)
        window.record(tokens)

    async def check_command_limit(
        self,
        client_id: str,
        count: int = 1
    ) -> RateLimitResult:
        """Check if command execution is within limit"""
        limits = self.get_limits(client_id)
        window = self._get_command_window(client_id)

        if not window.check_and_record(count):
            return RateLimitResult(
                allowed=False,
                limit_type="commands_per_hour",
                current=window.get_count(),
                limit=limits.commands_per_hour,
                retry_after=window.time_until_capacity(count),
                remaining=window.get_remaining()
            )

        return RateLimitResult(
            allowed=True,
            remaining=window.get_remaining()
        )

    async def check_all_limits(
        self,
        client_id: str,
        message_count: int = 1,
        estimated_tokens: int = 0,
        is_command: bool = False
    ) -> RateLimitResult:
        """
        Check all applicable limits at once.

        Returns the first limit that would be exceeded, or allowed=True.
        """
        # Check message limit
        msg_result = await self.check_message_limit(client_id, message_count)
        if not msg_result.allowed:
            return msg_result

        # Check token limit if estimated
        if estimated_tokens > 0:
            token_result = await self.check_token_limit(client_id, estimated_tokens)
            if not token_result.allowed:
                return token_result

        # Check command limit if applicable
        if is_command:
            cmd_result = await self.check_command_limit(client_id)
            if not cmd_result.allowed:
                return cmd_result

        return RateLimitResult(allowed=True)

    def get_client_status(self, client_id: str) -> Dict:
        """Get current rate limit status for a client"""
        limits = self.get_limits(client_id)

        status = {
            "client_id": client_id,
            "limits": {
                "messages_per_minute": limits.messages_per_minute,
                "messages_per_hour": limits.messages_per_hour,
                "tokens_per_day": limits.tokens_per_day,
                "commands_per_hour": limits.commands_per_hour,
            },
            "current": {}
        }

        if client_id in self.message_buckets:
            bucket = self.message_buckets[client_id]
            status["current"]["messages_burst_remaining"] = int(bucket.get_tokens())

        if client_id in self.message_windows:
            window = self.message_windows[client_id]
            status["current"]["messages_hourly_used"] = window.get_count()
            status["current"]["messages_hourly_remaining"] = window.get_remaining()

        if client_id in self.token_windows:
            window = self.token_windows[client_id]
            status["current"]["tokens_daily_used"] = window.get_count()
            status["current"]["tokens_daily_remaining"] = window.get_remaining()

        if client_id in self.command_windows:
            window = self.command_windows[client_id]
            status["current"]["commands_hourly_used"] = window.get_count()
            status["current"]["commands_hourly_remaining"] = window.get_remaining()

        return status

    def reset_client(self, client_id: str):
        """Reset all limits for a client"""
        if client_id in self.message_buckets:
            self.message_buckets[client_id].reset()
        if client_id in self.message_windows:
            self.message_windows[client_id].reset()
        if client_id in self.token_windows:
            self.token_windows[client_id].reset()
        if client_id in self.command_windows:
            self.command_windows[client_id].reset()

        logger.info(f"Reset all rate limits for client: {client_id}")

    def cleanup_inactive(self, inactive_hours: int = 24):
        """Remove limiters for clients inactive for specified hours"""
        # In a real implementation, you'd track last activity
        # For now, this is a placeholder
        pass
