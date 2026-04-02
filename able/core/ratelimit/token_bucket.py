"""
Token Bucket Rate Limiter

Classic token bucket algorithm for rate limiting.
"""

import time
import threading
from dataclasses import dataclass
from typing import Optional


@dataclass
class TokenBucketState:
    """Serializable state for persistence"""
    tokens: float
    last_update: float
    capacity: int
    refill_rate: float


class TokenBucket:
    """
    Token bucket rate limiter.

    Tokens are added at a constant rate up to a maximum capacity.
    Each operation consumes tokens.

    Example:
        bucket = TokenBucket(capacity=100, refill_rate=10)  # 100 max, 10/second
        if bucket.consume(1):
            # Operation allowed
        else:
            # Rate limited
    """

    def __init__(
        self,
        capacity: int,
        refill_rate: float,
        initial_tokens: Optional[float] = None
    ):
        """
        Initialize token bucket.

        Args:
            capacity: Maximum number of tokens
            refill_rate: Tokens added per second
            initial_tokens: Starting tokens (defaults to capacity)
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = initial_tokens if initial_tokens is not None else capacity
        self.last_update = time.time()
        self._lock = threading.Lock()

    def consume(self, tokens: int = 1) -> bool:
        """
        Try to consume tokens.

        Args:
            tokens: Number of tokens to consume

        Returns:
            True if tokens were consumed, False if insufficient
        """
        with self._lock:
            self._refill()

            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False

    def consume_or_wait(self, tokens: int = 1, max_wait: float = 10.0) -> float:
        """
        Consume tokens, waiting if necessary.

        Args:
            tokens: Number of tokens to consume
            max_wait: Maximum seconds to wait

        Returns:
            Seconds waited (0 if immediate), -1 if would exceed max_wait
        """
        with self._lock:
            self._refill()

            if self.tokens >= tokens:
                self.tokens -= tokens
                return 0.0

            # Calculate wait time
            needed = tokens - self.tokens
            wait_time = needed / self.refill_rate

            if wait_time > max_wait:
                return -1.0

            return wait_time

    def _refill(self):
        """Add tokens based on time elapsed"""
        now = time.time()
        elapsed = now - self.last_update
        self.last_update = now

        # Add tokens based on elapsed time
        new_tokens = elapsed * self.refill_rate
        self.tokens = min(self.capacity, self.tokens + new_tokens)

    def get_tokens(self) -> float:
        """Get current token count (after refill)"""
        with self._lock:
            self._refill()
            return self.tokens

    def get_state(self) -> TokenBucketState:
        """Get serializable state"""
        with self._lock:
            self._refill()
            return TokenBucketState(
                tokens=self.tokens,
                last_update=self.last_update,
                capacity=self.capacity,
                refill_rate=self.refill_rate
            )

    @classmethod
    def from_state(cls, state: TokenBucketState) -> 'TokenBucket':
        """Restore from serialized state"""
        bucket = cls(
            capacity=state.capacity,
            refill_rate=state.refill_rate,
            initial_tokens=state.tokens
        )
        bucket.last_update = state.last_update
        return bucket

    def reset(self):
        """Reset to full capacity"""
        with self._lock:
            self.tokens = self.capacity
            self.last_update = time.time()

    def time_until_tokens(self, tokens: int = 1) -> float:
        """Calculate seconds until specified tokens available"""
        with self._lock:
            self._refill()
            if self.tokens >= tokens:
                return 0.0
            needed = tokens - self.tokens
            return needed / self.refill_rate

    def __repr__(self) -> str:
        return f"TokenBucket(tokens={self.tokens:.1f}/{self.capacity}, rate={self.refill_rate}/s)"
