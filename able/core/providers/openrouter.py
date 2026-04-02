"""
OpenRouter Provider - Multi-model gateway with unified API.

Fallback provider with access to many models at competitive prices.
"""

import asyncio
import json
import logging
from typing import List, Dict, Optional, AsyncIterator

import aiohttp

from .base import (
    LLMProvider,
    ProviderConfig,
    ProviderError,
    Message,
    CompletionResult,
    UsageStats,
    ToolCall,
    Role,
)

logger = logging.getLogger(__name__)


class OpenRouterProvider(LLMProvider):
    """
    OpenRouter Provider for multi-model access.

    Default model: moonshot/moonshot-v1-auto (Kimi equivalent)
    Alternative models: anthropic/claude-3-opus, meta-llama/llama-3-70b, etc.
    """

    DEFAULT_MODEL = "moonshot/moonshot-v1-auto"
    BASE_URL = "https://openrouter.ai/api/v1"

    # Model pricing ($ per million tokens)
    MODEL_PRICING = {
        "moonshot/moonshot-v1-auto": {"input": 0.60, "output": 3.00},
        "anthropic/claude-3-opus": {"input": 15.00, "output": 75.00},
        "anthropic/claude-3-sonnet": {"input": 3.00, "output": 15.00},
        "anthropic/claude-3-haiku": {"input": 0.25, "output": 1.25},
        "meta-llama/llama-3-70b-instruct": {"input": 0.59, "output": 0.79},
        "google/gemini-pro": {"input": 0.125, "output": 0.375},
    }

    def __init__(
        self,
        api_key: str,
        model: str = None,
        base_url: str = None,
        site_url: str = "https://able.local",
        site_name: str = "ABLE Agent",
        timeout: float = 120.0
    ):
        model = model or self.DEFAULT_MODEL
        pricing = self.MODEL_PRICING.get(model, {"input": 1.0, "output": 5.0})

        config = ProviderConfig(
            api_key=api_key,
            base_url=base_url or self.BASE_URL,
            model=model,
            timeout=timeout,
            cost_per_million_input=pricing["input"],
            cost_per_million_output=pricing["output"]
        )
        super().__init__(config)

        self.site_url = site_url
        self.site_name = site_name
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def name(self) -> str:
        return "openrouter"

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            import ssl
            import certifi
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.config.timeout),
                connector=connector,
            )
        return self._session

    async def complete(
        self,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[str] = None,
        **kwargs
    ) -> CompletionResult:
        session = await self._get_session()

        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self.site_url,
            "X-Title": self.site_name
        }

        payload = {
            "model": self.config.model,
            "messages": self._convert_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if tools:
            payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice

        # OpenRouter specific options
        if kwargs.get("transforms"):
            payload["transforms"] = kwargs["transforms"]
        if kwargs.get("route"):
            payload["route"] = kwargs["route"]
        if kwargs.get("provider"):
            payload["provider"] = kwargs["provider"]
            
        # Optional massive context / extra routing args
        if kwargs.get("models"):
            payload["models"] = kwargs["models"]
        
        # Pass all other unhandled kwargs (like top_p, top_k, repetition_penalty) directly
        for key in ["top_p", "top_k", "presence_penalty", "repetition_penalty", "seed"]:
            if key in kwargs:
                payload[key] = kwargs[key]
                
        # Forward extra_body parameters if requested
        if kwargs.get("extra_body"):
            for k, v in kwargs["extra_body"].items():
                payload[k] = v

        try:
            async with session.post(
                f"{self.config.base_url}/chat/completions",
                headers=headers,
                json=payload
            ) as response:
                if response.status == 429:
                    # Check for retry-after header
                    retry_after = response.headers.get("Retry-After", "60")
                    raise ProviderError(
                        self.name,
                        f"Rate limited, retry after {retry_after}s",
                        retryable=True
                    )
                elif response.status == 402:
                    raise ProviderError(
                        self.name,
                        "Insufficient credits",
                        retryable=False
                    )
                elif response.status >= 500:
                    raise ProviderError(
                        self.name,
                        f"Server error: {response.status}",
                        retryable=True
                    )
                elif response.status != 200:
                    text = await response.text()
                    raise ProviderError(
                        self.name,
                        f"API error {response.status}: {text}",
                        retryable=False
                    )

                data = await response.json()

                # Check for OpenRouter error format
                if "error" in data:
                    raise ProviderError(
                        self.name,
                        data["error"].get("message", str(data["error"])),
                        retryable=False
                    )

                # Parse response
                choice = data["choices"][0]
                message = choice["message"]
                usage = data.get("usage", {})

                # Parse tool calls if present
                tool_calls = None
                if message.get("tool_calls"):
                    tool_calls = []
                    for tc in message["tool_calls"]:
                        args_raw = tc["function"]["arguments"]
                        parsed_args = {}
                        if isinstance(args_raw, str):
                            try:
                                parsed_args = json.loads(args_raw)
                            except json.JSONDecodeError:
                                # Provide a fallback instead of violently crashing if the AI truncates the JSON
                                parsed_args = {"error": f"JSONDecodeError: OpenRouter truncated the argument string: {args_raw[:100]}..." }
                        else:
                            parsed_args = args_raw
                            
                        tool_calls.append(ToolCall(
                            id=tc["id"],
                            name=tc["function"]["name"],
                            arguments=parsed_args
                        ))

                input_tokens = usage.get("prompt_tokens", 0)
                output_tokens = usage.get("completion_tokens", 0)

                result = CompletionResult(
                    content=message.get("content", ""),
                    finish_reason=choice.get("finish_reason", "stop"),
                    usage=UsageStats(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        total_tokens=input_tokens + output_tokens
                    ),
                    provider=self.name,
                    model=data.get("model", self.config.model),
                    tool_calls=tool_calls,
                    cost=self.calculate_cost(input_tokens, output_tokens),
                    raw_response=data
                )

                return result

        except aiohttp.ClientError as e:
            raise ProviderError(
                self.name,
                f"Connection error: {e}",
                retryable=True
            )

    async def stream(
        self,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs
    ) -> AsyncIterator[str]:
        session = await self._get_session()

        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self.site_url,
            "X-Title": self.site_name
        }

        payload = {
            "model": self.config.model,
            "messages": self._convert_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True
        }

        try:
            async with session.post(
                f"{self.config.base_url}/chat/completions",
                headers=headers,
                json=payload
            ) as response:
                if response.status != 200:
                    text = await response.text()
                    raise ProviderError(
                        self.name,
                        f"Stream error {response.status}: {text}",
                        retryable=response.status >= 500
                    )

                async for line in response.content:
                    line = line.decode('utf-8').strip()
                    if line.startswith('data: '):
                        data = line[6:]
                        if data == '[DONE]':
                            break
                        try:
                            chunk = json.loads(data)
                            if "error" in chunk:
                                raise ProviderError(
                                    self.name,
                                    chunk["error"].get("message", "Stream error"),
                                    retryable=False
                                )
                            delta = chunk['choices'][0].get('delta', {})
                            if content := delta.get('content'):
                                yield content
                        except json.JSONDecodeError:
                            continue

        except aiohttp.ClientError as e:
            raise ProviderError(
                self.name,
                f"Stream connection error: {e}",
                retryable=True
            )

    def count_tokens(self, text: str) -> int:
        """Approximate token count"""
        return len(text) // 4

    def switch_model(self, model: str):
        """Switch to a different model"""
        self.config.model = model
        pricing = self.MODEL_PRICING.get(model, {"input": 1.0, "output": 5.0})
        self.config.cost_per_million_input = pricing["input"]
        self.config.cost_per_million_output = pricing["output"]

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
