"""
Base classes for LLM Provider abstraction.

Inspired by PicoClaw's modular provider system.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, AsyncIterator, Union
from enum import Enum
import asyncio
import logging
import time

logger = logging.getLogger(__name__)


class ProviderError(Exception):
    """Base exception for provider errors"""
    def __init__(self, provider: str, message: str, retryable: bool = False):
        self.provider = provider
        self.retryable = retryable
        super().__init__(f"[{provider}] {message}")


class AllProvidersFailedError(Exception):
    """Raised when all providers in chain have failed"""
    def __init__(self, errors: List[ProviderError]):
        self.errors = errors
        messages = [str(e) for e in errors]
        super().__init__(f"All providers failed: {'; '.join(messages)}")


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class Message:
    """Normalized message format across all providers"""
    role: Role
    content: Union[str, List[Dict[str, Any]]]
    name: Optional[str] = None  # For tool messages
    tool_call_id: Optional[str] = None  # For tool responses
    tool_calls: Optional[List['ToolCall']] = None  # For assistant tool calls


@dataclass
class ToolCall:
    """Tool call request from model"""
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class UsageStats:
    """Token usage statistics"""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    @property
    def cost(self) -> float:
        """Calculate cost based on provider rates (set externally)"""
        return 0.0  # Calculated by provider


@dataclass
class CompletionResult:
    """Standardized completion result"""
    content: str
    finish_reason: str  # "stop", "length", "tool_calls", "error"
    usage: UsageStats
    provider: str
    model: str
    tool_calls: Optional[List[ToolCall]] = None
    latency_ms: float = 0.0
    cost: float = 0.0
    raw_response: Optional[Dict] = None

    def to_message(self) -> Message:
        """Convert result to assistant message"""
        return Message(
            role=Role.ASSISTANT,
            content=self.content,
            tool_calls=self.tool_calls
        )


@dataclass
class ProviderConfig:
    """Configuration for a provider"""
    api_key: str = ""
    base_url: Optional[str] = None
    model: str = ""
    timeout: float = 60.0
    max_retries: int = 3
    cost_per_million_input: float = 0.0
    cost_per_million_output: float = 0.0


class LLMProvider(ABC):
    """
    Abstract base class for all LLM providers.

    Implementations must handle:
    - Message format conversion
    - Token counting
    - Cost calculation
    - Error handling with retryable detection
    """

    def __init__(self, config: ProviderConfig):
        self.config = config
        self._client = None

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name for logging and identification"""
        pass

    @property
    def model(self) -> str:
        """Model being used"""
        return self.config.model

    @property
    def cost_per_million_input(self) -> float:
        return self.config.cost_per_million_input

    @property
    def cost_per_million_output(self) -> float:
        return self.config.cost_per_million_output

    @abstractmethod
    async def complete(
        self,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[str] = None,
        **kwargs
    ) -> CompletionResult:
        """
        Generate a completion from the model.

        Args:
            messages: Conversation history
            temperature: Sampling temperature (0-2)
            max_tokens: Maximum tokens to generate
            tools: Tool definitions for function calling
            tool_choice: "auto", "none", or specific tool name
            **kwargs: Provider-specific parameters

        Returns:
            CompletionResult with response and metadata

        Raises:
            ProviderError: On API errors (with retryable flag)
        """
        pass

    @abstractmethod
    async def stream(
        self,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs
    ) -> AsyncIterator[str]:
        """
        Stream completion tokens.

        Yields:
            String chunks as they arrive
        """
        pass

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        """
        Count tokens in text.

        Args:
            text: Text to count tokens for

        Returns:
            Approximate token count
        """
        pass

    def calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost in dollars"""
        input_cost = (input_tokens / 1_000_000) * self.cost_per_million_input
        output_cost = (output_tokens / 1_000_000) * self.cost_per_million_output
        return input_cost + output_cost

    async def health_check(self) -> bool:
        """Check if provider is healthy and responding"""
        try:
            result = await self.complete(
                [Message(role=Role.USER, content="Hi")],
                max_tokens=5,
                temperature=0
            )
            return result.finish_reason in ("stop", "length")
        except Exception:
            return False

    def _convert_messages(self, messages: List[Message]) -> List[Dict]:
        """Convert normalized messages to provider format (override if needed)"""
        import json
        result = []
        for msg in messages:
            converted = {
                "role": msg.role.value,
            }
            
            # OpenAI / OpenRouter schemas require content to be present. 
            if msg.role == Role.ASSISTANT and msg.tool_calls and not msg.content:
                # Force empty string instead of omitting it to prevent Go struct unmarshalling panics
                converted["content"] = ""
            else:
                converted["content"] = msg.content or ""

            # Name is not allowed on "tool" role messages in the modern OpenAI spec, only "tool_call_id"
            if msg.name and msg.role != Role.TOOL:
                converted["name"] = msg.name
                
            if msg.tool_call_id:
                converted["tool_call_id"] = msg.tool_call_id
                
            if msg.tool_calls:
                converted["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            # OpenAI spec REQUIRES arguments to be a JSON string, not a dict
                            "arguments": tc.arguments if isinstance(tc.arguments, str) else json.dumps(tc.arguments)
                        }
                    }
                    for tc in msg.tool_calls
                ]
            result.append(converted)
        return result


class CircuitBreaker:
    """
    Circuit breaker for provider failure detection.

    States:
      CLOSED  — healthy, requests flow normally
      OPEN    — broken, skip this provider entirely (fast-fail)
      HALF_OPEN — cooldown expired, allow one probe request

    Transitions:
      CLOSED → OPEN: after `failure_threshold` consecutive failures
      OPEN → HALF_OPEN: after `cooldown_seconds` elapse
      HALF_OPEN → CLOSED: probe succeeds
      HALF_OPEN → OPEN: probe fails (reset cooldown)
    """

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 300.0):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._failures: Dict[str, int] = {}          # provider_name → consecutive failures
        self._open_since: Dict[str, float] = {}      # provider_name → timestamp when opened
        self._total_trips: Dict[str, int] = {}        # provider_name → total times tripped

    def is_available(self, provider_name: str) -> bool:
        """Check if a provider should be tried (not in OPEN state)."""
        if provider_name not in self._open_since:
            return True  # CLOSED
        # Check if cooldown has elapsed → HALF_OPEN
        elapsed = time.time() - self._open_since[provider_name]
        if elapsed >= self.cooldown_seconds:
            return True  # HALF_OPEN — allow probe
        return False  # OPEN — skip

    def record_success(self, provider_name: str):
        """Record a successful call — reset to CLOSED."""
        self._failures.pop(provider_name, None)
        self._open_since.pop(provider_name, None)

    def record_failure(self, provider_name: str):
        """Record a failure — may trip to OPEN."""
        self._failures[provider_name] = self._failures.get(provider_name, 0) + 1
        if self._failures[provider_name] >= self.failure_threshold:
            self._open_since[provider_name] = time.time()
            self._total_trips[provider_name] = self._total_trips.get(provider_name, 0) + 1
            logger.warning(
                f"Circuit breaker OPEN for {provider_name} "
                f"({self._failures[provider_name]} consecutive failures, "
                f"cooldown={self.cooldown_seconds}s, "
                f"total_trips={self._total_trips[provider_name]})"
            )

    def get_status(self) -> Dict[str, str]:
        """Get circuit breaker state for all tracked providers."""
        status = {}
        now = time.time()
        for name in set(list(self._failures.keys()) + list(self._open_since.keys())):
            if name in self._open_since:
                elapsed = now - self._open_since[name]
                if elapsed >= self.cooldown_seconds:
                    status[name] = "half_open"
                else:
                    status[name] = f"open ({self.cooldown_seconds - elapsed:.0f}s remaining)"
            elif self._failures.get(name, 0) > 0:
                status[name] = f"closed ({self._failures[name]} failures)"
            else:
                status[name] = "closed"
        return status


class ProviderChain:
    """
    Chain of providers with automatic fallback and circuit breaker.

    Tries each provider in order until one succeeds.
    Tracks usage and costs across all providers.
    Circuit breaker skips providers that have failed repeatedly.
    """

    def __init__(
        self,
        providers: List[LLMProvider],
        retry_delay: float = 0.5,
        max_retries_per_provider: int = 1
    ):
        self.providers = providers
        self.retry_delay = retry_delay
        self.max_retries = max_retries_per_provider

        # Circuit breaker: skip dead providers instantly
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=3,   # 3 consecutive fails → open
            cooldown_seconds=300,  # 5 minutes before retry
        )

        # Usage tracking
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost = 0.0
        self.provider_usage: Dict[str, Dict] = {}

    async def complete(
        self,
        messages: List[Message],
        prefer_provider: Optional[str] = None,
        **kwargs
    ) -> CompletionResult:
        """
        Complete using provider chain with fallback and circuit breaker.

        Providers in OPEN state are skipped instantly (zero latency penalty).
        HALF_OPEN providers get one probe attempt.
        """
        errors = []

        # Reorder if preferred provider specified
        providers = self._order_providers(prefer_provider)

        for provider in providers:
            # Circuit breaker: skip providers that are known-broken
            if not self.circuit_breaker.is_available(provider.name):
                logger.debug(f"Skipping {provider.name} (circuit breaker OPEN)")
                continue

            for attempt in range(self.max_retries + 1):
                try:
                    start_time = time.time()
                    result = await provider.complete(messages, **kwargs)
                    result.latency_ms = (time.time() - start_time) * 1000

                    # Success — reset circuit breaker
                    self.circuit_breaker.record_success(provider.name)

                    # Track usage
                    self._track_usage(provider, result)

                    logger.info(
                        f"Completion via {provider.name}: "
                        f"{result.usage.total_tokens} tokens, "
                        f"${result.cost:.6f}, "
                        f"{result.latency_ms:.0f}ms"
                    )

                    return result

                except ProviderError as e:
                    errors.append(e)
                    self.circuit_breaker.record_failure(provider.name)
                    logger.warning(f"Provider {provider.name} failed (attempt {attempt + 1}): {e}")

                    if e.retryable and attempt < self.max_retries:
                        await asyncio.sleep(self.retry_delay * (2 ** attempt))
                        continue
                    else:
                        break  # Try next provider

                except Exception as e:
                    import traceback
                    error = ProviderError(provider.name, str(e), retryable=False)
                    errors.append(error)
                    self.circuit_breaker.record_failure(provider.name)
                    logger.error(
                        f"Unexpected error from {provider.name} "
                        f"(model={provider.config.model}): {type(e).__name__}: {e}\n"
                        f"{traceback.format_exc()}"
                    )
                    break  # Try next provider

        raise AllProvidersFailedError(errors)

    async def stream(
        self,
        messages: List[Message],
        prefer_provider: Optional[str] = None,
        **kwargs
    ) -> AsyncIterator[str]:
        """Stream completion with fallback (uses first available provider)"""
        providers = self._order_providers(prefer_provider)

        for provider in providers:
            try:
                async for chunk in provider.stream(messages, **kwargs):
                    yield chunk
                return  # Success, don't try other providers
            except Exception as e:
                logger.warning(f"Streaming failed for {provider.name}: {e}")
                continue

        raise AllProvidersFailedError([
            ProviderError(p.name, "Stream failed", False) for p in providers
        ])

    def _order_providers(self, prefer: Optional[str]) -> List[LLMProvider]:
        """Reorder providers to try preferred first"""
        if not prefer:
            return self.providers

        preferred = []
        others = []
        for p in self.providers:
            if p.name.lower() == prefer.lower():
                preferred.append(p)
            else:
                others.append(p)
        return preferred + others

    def _track_usage(self, provider: LLMProvider, result: CompletionResult):
        """Track cumulative usage across providers"""
        self.total_input_tokens += result.usage.input_tokens
        self.total_output_tokens += result.usage.output_tokens
        self.total_cost += result.cost

        if provider.name not in self.provider_usage:
            self.provider_usage[provider.name] = {
                'input_tokens': 0,
                'output_tokens': 0,
                'cost': 0.0,
                'requests': 0
            }

        self.provider_usage[provider.name]['input_tokens'] += result.usage.input_tokens
        self.provider_usage[provider.name]['output_tokens'] += result.usage.output_tokens
        self.provider_usage[provider.name]['cost'] += result.cost
        self.provider_usage[provider.name]['requests'] += 1

    def get_usage_report(self) -> Dict:
        """Get usage report across all providers"""
        return {
            'total': {
                'input_tokens': self.total_input_tokens,
                'output_tokens': self.total_output_tokens,
                'cost': self.total_cost
            },
            'by_provider': self.provider_usage
        }

    async def health_check_all(self) -> Dict[str, bool]:
        """Check health of all providers"""
        results = {}
        for provider in self.providers:
            results[provider.name] = await provider.health_check()
        return results
