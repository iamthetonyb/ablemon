"""
Ollama Provider - Local and hosted LLM inference.

Supports:
- Local Ollama instances (default)
- Hosted Ollama services with API key authentication
- Multiple model families: qwen, llama, mistral, etc.
"""

import asyncio
import json
import logging
import os
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


class ThinkingMode:
    """Qwen 3.5 thinking mode configuration"""
    OFF = "off"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    ULTRA = "ultra"


class QwenConfig:
    """
    Qwen 3.5 optimized configuration.

    Covers:
    - Thinking modes (off / low / medium / high / ultra)
    - YaRN context extension (128K → 1M tokens)
    - MoE routing (235B total, 22B active)
    - Optimal sampling parameters per mode
    """

    # Sampling parameters for thinking vs non-thinking
    THINKING_PARAMS = {
        "temperature": 0.6,
        "top_p": 0.95,
        "top_k": 20,
        "min_p": 0.0,
    }

    NON_THINKING_PARAMS = {
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 20,
        "presence_penalty": 1.5,
    }

    # Thinking budget tokens per mode
    THINKING_BUDGETS = {
        ThinkingMode.OFF: 0,
        ThinkingMode.LOW: 8_192,
        ThinkingMode.MEDIUM: 16_384,
        ThinkingMode.HIGH: 32_768,
        ThinkingMode.ULTRA: 81_920,
    }

    # YaRN context window configurations
    CONTEXT_CONFIGS = {
        "default": {"num_ctx": 32_768},
        "extended": {"num_ctx": 131_072, "rope_scaling_type": "yarn", "rope_scaling_factor": 2.0},
        "long": {"num_ctx": 262_144, "rope_scaling_type": "yarn", "rope_scaling_factor": 4.0},
        "max": {"num_ctx": 1_048_576, "rope_scaling_type": "yarn", "rope_scaling_factor": 4.0},
    }

    @classmethod
    def get_options(
        cls,
        thinking_mode: str = ThinkingMode.OFF,
        context_size: str = "default",
        extra_options: dict = None,
    ) -> dict:
        """
        Build Ollama options dict for Qwen 3.5.

        Args:
            thinking_mode: ThinkingMode constant or "off"/"low"/"medium"/"high"/"ultra"
            context_size: "default" (32K) / "extended" (128K) / "long" (262K) / "max" (1M)
            extra_options: Additional options to merge

        Returns:
            Options dict suitable for Ollama API payload["options"]
        """
        if thinking_mode == ThinkingMode.OFF:
            params = cls.NON_THINKING_PARAMS.copy()
        else:
            params = cls.THINKING_PARAMS.copy()
            budget = cls.THINKING_BUDGETS.get(thinking_mode, 0)
            if budget:
                params["num_predict"] = budget

        ctx = cls.CONTEXT_CONFIGS.get(context_size, cls.CONTEXT_CONFIGS["default"])
        params.update(ctx)

        if extra_options:
            params.update(extra_options)

        return params

    @classmethod
    def auto_thinking_mode(cls, complexity_score: float) -> str:
        """Auto-select thinking mode based on task complexity (0.0–1.0)"""
        if complexity_score < 0.3:
            return ThinkingMode.OFF
        elif complexity_score < 0.5:
            return ThinkingMode.LOW
        elif complexity_score < 0.7:
            return ThinkingMode.MEDIUM
        elif complexity_score < 0.9:
            return ThinkingMode.HIGH
        else:
            return ThinkingMode.ULTRA

    @classmethod
    def thinking_system_prefix(cls, mode: str) -> str:
        """
        Prefix to add to system prompt to enable thinking mode in Qwen.
        Qwen 3.5 uses /think and /no_think directives.
        """
        if mode == ThinkingMode.OFF:
            return "/no_think\n"
        else:
            return "/think\n"


class OllamaProvider(LLMProvider):
    """
    Ollama Provider for local and hosted LLM inference.

    Supported models:
    - qwen3.5 / qwen2.5 (recommended for ABLE)
    - llama3.2 / llama3.1
    - mistral / mixtral
    - codellama
    - phi3

    Can connect to:
    - Local Ollama: http://localhost:11434
    - Hosted Ollama: Any URL with API key authentication

    Benefits:
    - Free when running locally
    - Private (data stays on your infrastructure)
    - API-compatible with hosted services
    - Supports many model families
    """

    DEFAULT_MODEL = "qwen3"  # Updated to Qwen 3 for latest capabilities
    DEFAULT_URL = "http://localhost:11434"

    # Model aliases for convenience
    # IMPORTANT: Ensure model names match what's available on your Ollama instance
    MODEL_ALIASES = {
        "qwen3.5": "qwen3:latest",      # Qwen 3.5 -> Qwen 3 (latest)
        "qwen3": "qwen3:latest",         # Qwen 3 explicit
        "qwen2.5": "qwen2.5:latest",     # Qwen 2.5 for compatibility
        "qwen": "qwen3:latest",          # Default qwen -> latest (Qwen 3)
        "llama": "llama3.2:latest",
        "llama3": "llama3.2:latest",
        "mistral": "mistral:latest",
        "codellama": "codellama:latest",
        "phi": "phi3:latest",
        "gemma": "gemma2:latest",
    }

    def __init__(
        self,
        model: str = None,
        base_url: str = None,
        api_key: str = None,
        api_key_env: str = "OLLAMA_API_KEY",
        timeout: float = 300.0,
        cost_per_million_input: float = 0.0,  # Usually free
        cost_per_million_output: float = 0.0,
    ):
        # Resolve API key from env if not provided
        resolved_api_key = api_key or os.environ.get(api_key_env, "")

        # Resolve model alias
        resolved_model = self.MODEL_ALIASES.get(model, model) if model else self.DEFAULT_MODEL

        config = ProviderConfig(
            api_key=resolved_api_key,
            base_url=base_url or self.DEFAULT_URL,
            model=resolved_model,
            timeout=timeout,
            cost_per_million_input=cost_per_million_input,
            cost_per_million_output=cost_per_million_output
        )
        super().__init__(config)
        self._session: Optional[aiohttp.ClientSession] = None
        self._is_hosted = bool(resolved_api_key)

        if self._is_hosted:
            logger.info(f"Ollama configured for hosted endpoint: {config.base_url}")
        else:
            logger.info(f"Ollama configured for local endpoint: {config.base_url}")

    @property
    def name(self) -> str:
        return "ollama_hosted" if self._is_hosted else "ollama"

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {}
            # Add auth header for hosted services
            if self.config.api_key:
                headers["Authorization"] = f"Bearer {self.config.api_key}"
            self._session = aiohttp.ClientSession(
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.config.timeout)
            )
        return self._session

    def _convert_messages(self, messages: List[Message]) -> List[Dict]:
        """Convert to Ollama chat format"""
        converted = []
        for msg in messages:
            converted.append({
                "role": msg.role.value,
                "content": msg.content
            })
        return converted

    def _is_qwen_model(self) -> bool:
        """Check if the current model is a Qwen variant"""
        return "qwen" in self.config.model.lower()

    async def complete(
        self,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[str] = None,
        thinking_mode: str = ThinkingMode.OFF,
        context_size: str = "default",
        complexity_score: float = 0.0,
        **kwargs
    ) -> CompletionResult:
        session = await self._get_session()

        # Auto-select thinking mode for Qwen based on complexity
        if self._is_qwen_model() and complexity_score > 0 and thinking_mode == ThinkingMode.OFF:
            thinking_mode = QwenConfig.auto_thinking_mode(complexity_score)

        # Build options — use QwenConfig for Qwen models
        if self._is_qwen_model():
            options = QwenConfig.get_options(
                thinking_mode=thinking_mode,
                context_size=context_size,
                extra_options={"num_predict": max_tokens},
            )
            # Apply thinking prefix to system message if needed
            prefix = QwenConfig.thinking_system_prefix(thinking_mode)
            if prefix and messages and messages[0].role == Role.SYSTEM:
                messages = list(messages)
                messages[0] = Message(
                    role=messages[0].role,
                    content=prefix + messages[0].content,
                )
        else:
            options = {
                "temperature": temperature,
                "num_predict": max_tokens,
            }

        payload = {
            "model": self.config.model,
            "messages": self._convert_messages(messages),
            "stream": False,
            "options": options,
        }

        # Ollama tool support is model-dependent
        if tools and self._model_supports_tools():
            payload["tools"] = tools

        try:
            async with session.post(
                f"{self.config.base_url}/api/chat",
                json=payload
            ) as response:
                if response.status == 404:
                    raise ProviderError(
                        self.name,
                        f"Model '{self.config.model}' not found. Run: ollama pull {self.config.model}",
                        retryable=False
                    )
                elif response.status != 200:
                    text = await response.text()
                    raise ProviderError(
                        self.name,
                        f"API error {response.status}: {text}",
                        retryable=response.status >= 500
                    )

                data = await response.json()

                message = data.get("message", {})

                # Parse tool calls if present
                tool_calls = None
                if message.get("tool_calls"):
                    tool_calls = [
                        ToolCall(
                            id=f"call_{i}",
                            name=tc["function"]["name"],
                            arguments=tc["function"]["arguments"]
                        )
                        for i, tc in enumerate(message["tool_calls"])
                    ]

                # Ollama provides token counts
                prompt_eval_count = data.get("prompt_eval_count", 0)
                eval_count = data.get("eval_count", 0)

                result = CompletionResult(
                    content=message.get("content", ""),
                    finish_reason="stop" if data.get("done") else "length",
                    usage=UsageStats(
                        input_tokens=prompt_eval_count,
                        output_tokens=eval_count,
                        total_tokens=prompt_eval_count + eval_count
                    ),
                    provider=self.name,
                    model=data.get("model", self.config.model),
                    tool_calls=tool_calls,
                    cost=0.0,  # Free
                    raw_response=data
                )

                return result

        except aiohttp.ClientConnectorError:
            raise ProviderError(
                self.name,
                f"Cannot connect to Ollama at {self.config.base_url}. Is it running?",
                retryable=True
            )
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

        payload = {
            "model": self.config.model,
            "messages": self._convert_messages(messages),
            "stream": True,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            }
        }

        try:
            async with session.post(
                f"{self.config.base_url}/api/chat",
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
                    try:
                        data = json.loads(line.decode('utf-8'))
                        if message := data.get("message", {}).get("content"):
                            yield message
                        if data.get("done"):
                            break
                    except json.JSONDecodeError:
                        continue

        except aiohttp.ClientConnectorError:
            raise ProviderError(
                self.name,
                f"Cannot connect to Ollama at {self.config.base_url}",
                retryable=True
            )
        except aiohttp.ClientError as e:
            raise ProviderError(
                self.name,
                f"Stream connection error: {e}",
                retryable=True
            )

    def count_tokens(self, text: str) -> int:
        """Approximate token count"""
        return len(text) // 4

    def _model_supports_tools(self) -> bool:
        """Check if current model supports tool calling"""
        tool_capable = ['llama3', 'mistral', 'mixtral', 'command-r']
        return any(m in self.config.model.lower() for m in tool_capable)

    async def list_models(self) -> List[str]:
        """List available local models"""
        session = await self._get_session()
        try:
            async with session.get(f"{self.config.base_url}/api/tags") as response:
                if response.status == 200:
                    data = await response.json()
                    return [m["name"] for m in data.get("models", [])]
        except Exception:
            pass
        return []

    async def pull_model(self, model: str) -> bool:
        """Pull a model from Ollama registry"""
        session = await self._get_session()
        try:
            async with session.post(
                f"{self.config.base_url}/api/pull",
                json={"name": model, "stream": False}
            ) as response:
                return response.status == 200
        except Exception:
            return False

    async def health_check(self) -> bool:
        """Check if Ollama is running and model is available"""
        session = await self._get_session()
        try:
            async with session.get(f"{self.config.base_url}/api/tags") as response:
                if response.status == 200:
                    data = await response.json()
                    models = [m["name"] for m in data.get("models", [])]
                    return self.config.model in models or any(
                        self.config.model in m for m in models
                    )
        except Exception:
            pass
        return False

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
