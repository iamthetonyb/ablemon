"""
NVIDIA NIM Provider - Free tier LLM access via NVIDIA API.

Primary provider in the ATLAS chain due to free tier availability.
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


class NVIDIANIMProvider(LLMProvider):
    """
    NVIDIA NIM Provider using the integrate.api.nvidia.com endpoint.

    Default model: moonshot-v1-auto (Kimi K2.5 equivalent)
    Cost: Free tier available
    """

    DEFAULT_MODEL = "moonshot-v1-auto"
    BASE_URL = "https://integrate.api.nvidia.com/v1"

    def __init__(
        self,
        api_key: str,
        model: str = None,
        base_url: str = None,
        timeout: float = 120.0
    ):
        config = ProviderConfig(
            api_key=api_key,
            base_url=base_url or self.BASE_URL,
            model=model or self.DEFAULT_MODEL,
            timeout=timeout,
            cost_per_million_input=0.0,  # Free tier
            cost_per_million_output=0.0
        )
        super().__init__(config)
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def name(self) -> str:
        return "nvidia_nim"

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
            "Content-Type": "application/json"
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

        if "chat_template_kwargs" in kwargs:
            payload["chat_template_kwargs"] = kwargs["chat_template_kwargs"]
        if "top_p" in kwargs:
            payload["top_p"] = kwargs["top_p"]
        if "presence_penalty" in kwargs:
            payload["presence_penalty"] = kwargs["presence_penalty"]

        # NIM requires these inside nvext, not at root level
        nvext = {}
        if "repetition_penalty" in kwargs:
            nvext["repetition_penalty"] = kwargs["repetition_penalty"]
        if "top_k" in kwargs:
            nvext["top_k"] = kwargs["top_k"]
        if nvext:
            payload["nvext"] = nvext

        try:
            async with session.post(
                f"{self.config.base_url}/chat/completions",
                headers=headers,
                json=payload
            ) as response:
                if response.status == 429:
                    raise ProviderError(
                        self.name,
                        "Rate limited",
                        retryable=True
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

                # Parse response
                choice = data["choices"][0]
                message = choice["message"]
                usage = data.get("usage", {})

                # Parse tool calls if present
                tool_calls = None
                if message.get("tool_calls"):
                    tool_calls = [
                        ToolCall(
                            id=tc["id"],
                            name=tc["function"]["name"],
                            arguments=json.loads(tc["function"]["arguments"])
                            if isinstance(tc["function"]["arguments"], str)
                            else tc["function"]["arguments"]
                        )
                        for tc in message["tool_calls"]
                    ]

                result = CompletionResult(
                    content=message.get("content", ""),
                    finish_reason=choice.get("finish_reason", "stop"),
                    usage=UsageStats(
                        input_tokens=usage.get("prompt_tokens", 0),
                        output_tokens=usage.get("completion_tokens", 0),
                        total_tokens=usage.get("total_tokens", 0)
                    ),
                    provider=self.name,
                    model=self.config.model,
                    tool_calls=tool_calls,
                    cost=0.0,  # Free tier
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
            "Content-Type": "application/json"
        }

        payload = {
            "model": self.config.model,
            "messages": self._convert_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True
        }
        
        if "chat_template_kwargs" in kwargs:
            payload["chat_template_kwargs"] = kwargs["chat_template_kwargs"]
        if "top_p" in kwargs:
            payload["top_p"] = kwargs["top_p"]
        if "presence_penalty" in kwargs:
            payload["presence_penalty"] = kwargs["presence_penalty"]

        # NIM requires these inside nvext, not at root level
        nvext = {}
        if "repetition_penalty" in kwargs:
            nvext["repetition_penalty"] = kwargs["repetition_penalty"]
        if "top_k" in kwargs:
            nvext["top_k"] = kwargs["top_k"]
        if nvext:
            payload["nvext"] = nvext

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
        """Approximate token count (4 chars per token average)"""
        return len(text) // 4

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
