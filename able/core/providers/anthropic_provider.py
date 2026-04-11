"""
Anthropic Provider - Premium Claude access for complex reasoning.

Used for: complex reasoning, legal review, sensitive communications.
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


class AnthropicProvider(LLMProvider):
    """
    Anthropic Claude Provider for premium inference.

    Default model: claude-sonnet-4-6 (latest Sonnet — standard workloads)
    Premium model: claude-opus-4-6 (latest Opus — critical thinking, planning)

    Model routing: Opus for complex reasoning/planning, Sonnet for execution.
    """

    DEFAULT_MODEL = "claude-sonnet-4-6"
    PREMIUM_MODEL = "claude-opus-4-6"
    BASE_URL = "https://api.anthropic.com/v1"
    API_VERSION = "2023-06-01"
    ADVISOR_TOOL_TYPE = "advisor_20260301"

    # Model pricing ($ per million tokens)
    MODEL_PRICING = {
        "claude-opus-4-6": {"input": 15.00, "output": 75.00},
        "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
        "claude-opus-4-5-20251101": {"input": 15.00, "output": 75.00},
        "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
        "claude-3-opus-20240229": {"input": 15.00, "output": 75.00},
        "claude-3-sonnet-20240229": {"input": 3.00, "output": 15.00},
        "claude-3-haiku-20240307": {"input": 0.25, "output": 1.25},
        "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    }

    def __init__(
        self,
        api_key: str,
        model: str = None,
        timeout: float = 180.0,
        use_premium: bool = False,
        extended_thinking: bool = False,
        thinking_budget_tokens: int = 16000,
        prompt_caching: bool = True,
    ):
        model = model or (self.PREMIUM_MODEL if use_premium else self.DEFAULT_MODEL)
        pricing = self.MODEL_PRICING.get(model, {"input": 5.00, "output": 25.00})

        config = ProviderConfig(
            api_key=api_key,
            base_url=self.BASE_URL,
            model=model,
            timeout=timeout,
            cost_per_million_input=pricing["input"],
            cost_per_million_output=pricing["output"]
        )
        super().__init__(config)
        self._session: Optional[aiohttp.ClientSession] = None
        self.extended_thinking = extended_thinking
        self.thinking_budget_tokens = thinking_budget_tokens
        self._prompt_caching = prompt_caching
        # E3: Cache hit/miss stats for observability
        self.cache_stats = {"creation_tokens": 0, "read_tokens": 0, "hits": 0, "misses": 0}

    @classmethod
    def advisor_tool(cls, max_uses: int = 3, advisor_model: str = None) -> Dict:
        """Return the advisor tool declaration for Anthropic's advisor strategy.

        The advisor_20260301 server-side tool lets a cost-effective executor
        (Sonnet/Haiku) escalate to a frontier advisor (Opus) within a single
        /v1/messages call — no orchestration overhead.

        Args:
            max_uses: Maximum advisor invocations per request (cost control).
            advisor_model: Override advisor model (defaults to PREMIUM_MODEL).
        """
        return {
            "type": cls.ADVISOR_TOOL_TYPE,
            "name": "advisor",
            "advisor_model": advisor_model or cls.PREMIUM_MODEL,
            "max_uses": max_uses,
        }

    @property
    def name(self) -> str:
        return "anthropic"

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.config.timeout)
            )
        return self._session

    def _convert_messages(
        self,
        messages: List[Message],
        *,
        enable_cache: bool = False,
        cache_breakpoints: int = 2,
    ) -> tuple:
        """Convert to Anthropic format.

        Returns (system_content, messages_list).

        When *enable_cache* is True, adds ``cache_control`` markers to the
        system prompt and the first *cache_breakpoints* conversation turns.
        Anthropic's prompt cache keeps a prefix hash — identical prefixes
        across requests hit the cache automatically, reducing cost and
        latency on multi-turn sessions (~90% input token savings on cache
        hits).  See: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching

        The system prompt is returned as a list of content blocks (with
        cache_control) when caching is enabled, or as a plain string when not.
        """
        system = None
        converted = []

        for msg in messages:
            if msg.role == Role.SYSTEM:
                if enable_cache:
                    # Structured system block with cache_control
                    system = [
                        {
                            "type": "text",
                            "text": msg.content,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ]
                else:
                    system = msg.content
            elif msg.role == Role.USER:
                converted.append({
                    "role": "user",
                    "content": msg.content
                })
            elif msg.role == Role.ASSISTANT:
                content = []
                if msg.content:
                    content.append({"type": "text", "text": msg.content})
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        content.append({
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments
                        })
                converted.append({
                    "role": "assistant",
                    "content": content if len(content) > 1 else msg.content
                })
            elif msg.role == Role.TOOL:
                converted.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.tool_call_id,
                        "content": msg.content
                    }]
                })

        # Add cache_control to the first N conversation turns so the
        # prefix up to that point is cached server-side.
        if enable_cache and converted:
            _marked = 0
            for entry in converted:
                if _marked >= cache_breakpoints:
                    break
                content = entry.get("content")
                if isinstance(content, str) and content:
                    entry["content"] = [
                        {
                            "type": "text",
                            "text": content,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ]
                    _marked += 1
                elif isinstance(content, list) and content:
                    # Tag the last block in the list
                    content[-1]["cache_control"] = {"type": "ephemeral"}
                    _marked += 1

        return system, converted

    def _convert_tools(self, tools: List[Dict]) -> List[Dict]:
        """Convert OpenAI tool format to Anthropic format.

        Advisor tools (type=advisor_20260301) pass through unchanged —
        they're server-side tools handled by Anthropic's API.
        """
        converted = []
        for tool in tools:
            if tool.get("type") == self.ADVISOR_TOOL_TYPE:
                # Advisor tool — pass through as-is (server-side)
                converted.append(tool)
            elif tool.get("type") == "function":
                func = tool["function"]
                converted.append({
                    "name": func["name"],
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {"type": "object", "properties": {}})
                })
            else:
                converted.append(tool)
        return converted

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

        # E3: prompt caching — opt-in via kwarg or instance default
        _enable_cache = kwargs.pop("enable_cache", self._prompt_caching)

        headers = {
            "x-api-key": self.config.api_key,
            "anthropic-version": self.API_VERSION,
            "Content-Type": "application/json"
        }

        # Extended thinking requires beta header
        _betas = []
        if self.extended_thinking:
            _betas.append("interleaved-thinking-2025-05-14")
        if _enable_cache:
            _betas.append("prompt-caching-2024-07-31")
        if _betas:
            headers["anthropic-beta"] = ",".join(_betas)

        system, converted_messages = self._convert_messages(
            messages, enable_cache=_enable_cache
        )

        payload = {
            "model": self.config.model,
            "messages": converted_messages,
            "max_tokens": max_tokens,
        }

        # Extended thinking: temperature must be 1, add thinking budget
        if self.extended_thinking:
            payload["temperature"] = 1
            payload["thinking"] = {
                "type": "enabled",
                "budget_tokens": self.thinking_budget_tokens,
            }
        else:
            payload["temperature"] = temperature

        if system:
            payload["system"] = system

        if tools:
            payload["tools"] = self._convert_tools(tools)
            if tool_choice:
                if tool_choice == "auto":
                    payload["tool_choice"] = {"type": "auto"}
                elif tool_choice == "none":
                    payload["tool_choice"] = {"type": "none"}
                else:
                    payload["tool_choice"] = {"type": "tool", "name": tool_choice}

        try:
            async with session.post(
                f"{self.config.base_url}/messages",
                headers=headers,
                json=payload
            ) as response:
                if response.status == 429:
                    raise ProviderError(
                        self.name,
                        "Rate limited",
                        retryable=True
                    )
                elif response.status == 529:
                    raise ProviderError(
                        self.name,
                        "API overloaded",
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

                # Parse content blocks
                content_text = ""
                thinking_text = ""
                tool_calls = []

                for block in data.get("content", []):
                    if block["type"] == "text":
                        content_text += block["text"]
                    elif block["type"] == "thinking":
                        thinking_text += block.get("thinking", "")
                    elif block["type"] == "tool_use":
                        tool_calls.append(ToolCall(
                            id=block["id"],
                            name=block["name"],
                            arguments=block["input"]
                        ))

                usage = data.get("usage", {})
                input_tokens = usage.get("input_tokens", 0)
                output_tokens = usage.get("output_tokens", 0)

                # E3: Track prompt cache performance
                _cache_creation = usage.get("cache_creation_input_tokens", 0)
                _cache_read = usage.get("cache_read_input_tokens", 0)
                if _cache_creation or _cache_read:
                    self.cache_stats["creation_tokens"] += _cache_creation
                    self.cache_stats["read_tokens"] += _cache_read
                    if _cache_read > 0:
                        self.cache_stats["hits"] += 1
                    else:
                        self.cache_stats["misses"] += 1
                    logger.info(
                        "Prompt cache: %d created, %d read (cumulative: %d hits, %d misses)",
                        _cache_creation, _cache_read,
                        self.cache_stats["hits"], self.cache_stats["misses"],
                    )

                # Advisor strategy: track advisor token usage separately
                advisor_usage = None
                advisor_data = usage.get("advisor_usage")
                if advisor_data:
                    advisor_usage = {
                        "calls": advisor_data.get("calls", 0),
                        "input_tokens": advisor_data.get("input_tokens", 0),
                        "output_tokens": advisor_data.get("output_tokens", 0),
                    }
                    logger.info(
                        "Advisor usage: %d calls, %d in / %d out tokens",
                        advisor_usage["calls"],
                        advisor_usage["input_tokens"],
                        advisor_usage["output_tokens"],
                    )

                result = CompletionResult(
                    content=content_text,
                    finish_reason=data.get("stop_reason", "end_turn"),
                    usage=UsageStats(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        total_tokens=input_tokens + output_tokens
                    ),
                    provider=self.name,
                    model=data.get("model", self.config.model),
                    tool_calls=tool_calls if tool_calls else None,
                    cost=self.calculate_cost(input_tokens, output_tokens),
                    raw_response=data,
                    thinking_content=thinking_text if thinking_text else None,
                    advisor_usage=advisor_usage,
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
        tools: Optional[List[Dict]] = None,
        **kwargs
    ) -> AsyncIterator[str]:
        session = await self._get_session()

        _enable_cache = kwargs.pop("enable_cache", self._prompt_caching)

        headers = {
            "x-api-key": self.config.api_key,
            "anthropic-version": self.API_VERSION,
            "Content-Type": "application/json"
        }

        _betas = []
        if self.extended_thinking:
            _betas.append("interleaved-thinking-2025-05-14")
        if _enable_cache:
            _betas.append("prompt-caching-2024-07-31")
        if _betas:
            headers["anthropic-beta"] = ",".join(_betas)

        system, converted_messages = self._convert_messages(
            messages, enable_cache=_enable_cache
        )

        payload = {
            "model": self.config.model,
            "messages": converted_messages,
            "max_tokens": max_tokens,
            "stream": True
        }

        # Extended thinking: temperature must be 1, add thinking budget
        if self.extended_thinking:
            payload["temperature"] = 1
            payload["thinking"] = {
                "type": "enabled",
                "budget_tokens": self.thinking_budget_tokens,
            }
        else:
            payload["temperature"] = temperature

        if system:
            payload["system"] = system

        if tools:
            payload["tools"] = self._convert_tools(tools)

        # Track tool calls accumulated during streaming for callers that need them
        _pending_tool_calls: List[Dict] = []
        _current_tool: Optional[Dict] = None

        try:
            async with session.post(
                f"{self.config.base_url}/messages",
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
                        try:
                            data = json.loads(line[6:])
                            event_type = data.get("type")

                            if event_type == "content_block_start":
                                block = data.get("content_block", {})
                                if block.get("type") == "tool_use":
                                    _current_tool = {
                                        "id": block.get("id", ""),
                                        "name": block.get("name", ""),
                                        "input_json": "",
                                    }
                            elif event_type == "content_block_delta":
                                delta = data.get("delta", {})
                                delta_type = delta.get("type")
                                if delta_type == "text_delta":
                                    yield delta.get("text", "")
                                elif delta_type == "thinking_delta":
                                    # Yield thinking wrapped in markers for downstream filtering
                                    thinking = delta.get("thinking", "")
                                    if thinking:
                                        yield f"<think>{thinking}</think>"
                                elif delta_type == "input_json_delta" and _current_tool:
                                    _current_tool["input_json"] += delta.get("partial_json", "")
                            elif event_type == "content_block_stop":
                                if _current_tool:
                                    try:
                                        args = json.loads(_current_tool["input_json"]) if _current_tool["input_json"] else {}
                                    except json.JSONDecodeError:
                                        args = {}
                                    _pending_tool_calls.append({
                                        "id": _current_tool["id"],
                                        "name": _current_tool["name"],
                                        "arguments": args,
                                    })
                                    _current_tool = None
                            elif event_type == "message_stop":
                                break
                            elif event_type == "error":
                                raise ProviderError(
                                    self.name,
                                    data.get("error", {}).get("message", "Stream error"),
                                    retryable=False
                                )
                        except json.JSONDecodeError:
                            continue

        except aiohttp.ClientError as e:
            raise ProviderError(
                self.name,
                f"Stream connection error: {e}",
                retryable=True
            )

        # Expose accumulated tool calls for callers that need them
        # (e.g., gateway stream_message tool dispatch)
        self._last_stream_tool_calls = _pending_tool_calls

    def count_tokens(self, text: str) -> int:
        """Approximate token count for Claude"""
        # Claude tokenizer is similar to GPT but slightly more efficient
        return int(len(text) / 3.5)

    def use_premium_model(self):
        """Switch to premium Opus model"""
        self.config.model = self.PREMIUM_MODEL
        pricing = self.MODEL_PRICING[self.PREMIUM_MODEL]
        self.config.cost_per_million_input = pricing["input"]
        self.config.cost_per_million_output = pricing["output"]

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
