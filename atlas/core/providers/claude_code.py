"""
Claude Code Provider — Uses Claude Code CLI subscription for inference.

When ATLAS runs inside Claude Code or on a machine with Claude Code installed,
this provider delegates inference to claude CLI subagents. This is FREE on a
Claude Code subscription (Max/Team/Enterprise) — no API costs.

For planning/orchestration: uses Opus (the current session model)
For execution delegation: spawns --model sonnet subagents
"""

import asyncio
import json
import logging
import shutil
from typing import List, Dict, Optional, AsyncIterator

from .base import (
    LLMProvider,
    ProviderConfig,
    ProviderError,
    Message,
    CompletionResult,
    UsageStats,
    Role,
)

logger = logging.getLogger(__name__)


class ClaudeCodeProvider(LLMProvider):
    """
    Provider that delegates to Claude Code CLI for inference.

    Uses your Claude Code plan subscription — zero API costs.
    Spawns claude subprocesses with appropriate model flags.

    Routing:
    - premium=True → claude (Opus, current session)
    - premium=False → claude --model sonnet (cheaper, faster)
    """

    def __init__(
        self,
        model: str = "sonnet",
        timeout: float = 300.0,
    ):
        # Claude Code CLI is free on subscription — $0 per token
        config = ProviderConfig(
            api_key="claude-code-subscription",
            base_url="local://claude-code",
            model=model,
            timeout=timeout,
            cost_per_million_input=0.0,
            cost_per_million_output=0.0,
        )
        super().__init__(config)
        self._claude_path = shutil.which("claude") or "claude"
        self._available: Optional[bool] = None

    @property
    def name(self) -> str:
        return "claude-code"

    async def _check_available(self) -> bool:
        """Check if claude CLI is available"""
        if self._available is not None:
            return self._available
        try:
            proc = await asyncio.create_subprocess_exec(
                self._claude_path, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            self._available = proc.returncode == 0
        except Exception:
            self._available = False
        return self._available

    def _build_prompt(self, messages: List[Message]) -> str:
        """Convert message list to a single prompt string for claude CLI"""
        parts = []
        for msg in messages:
            if msg.role == Role.SYSTEM:
                parts.append(f"[System Instructions]\n{msg.content}\n")
            elif msg.role == Role.USER:
                parts.append(f"[User]\n{msg.content}\n")
            elif msg.role == Role.ASSISTANT:
                parts.append(f"[Assistant]\n{msg.content}\n")
        return "\n".join(parts)

    async def complete(
        self,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[str] = None,
        **kwargs
    ) -> CompletionResult:
        if not await self._check_available():
            raise ProviderError(
                self.name,
                "Claude Code CLI not available. Install from https://docs.anthropic.com/claude-code",
                retryable=False,
            )

        prompt = self._build_prompt(messages)

        # Build command
        cmd = [self._claude_path, "--print", "--output-format", "json"]

        # Model selection: default to sonnet for cost efficiency
        model = kwargs.get("model_override", self.config.model)
        if model and model != "opus":
            cmd.extend(["--model", model])

        # Max turns for agentic tasks
        max_turns = kwargs.get("max_turns", 5)
        cmd.extend(["--max-turns", str(max_turns)])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=prompt.encode("utf-8")),
                timeout=self.config.timeout,
            )

            if proc.returncode != 0:
                error_msg = stderr.decode("utf-8", errors="replace")
                raise ProviderError(
                    self.name,
                    f"Claude CLI exited with code {proc.returncode}: {error_msg[:500]}",
                    retryable=proc.returncode in (1, 137),  # retry on crash/OOM
                )

            output = stdout.decode("utf-8", errors="replace")

            # Try to parse as JSON (--output-format json)
            try:
                data = json.loads(output)
                content = data.get("result", data.get("text", output))
                input_tokens = data.get("input_tokens", 0)
                output_tokens = data.get("output_tokens", 0)
                cost = data.get("cost_usd", 0.0)
            except json.JSONDecodeError:
                content = output
                input_tokens = self.count_tokens(prompt)
                output_tokens = self.count_tokens(content)
                cost = 0.0  # Free on subscription

            return CompletionResult(
                content=content,
                finish_reason="end_turn",
                usage=UsageStats(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=input_tokens + output_tokens,
                ),
                provider=self.name,
                model=f"claude-code:{model}",
                tool_calls=None,
                cost=cost,
                raw_response={"output": content[:1000]},
            )

        except asyncio.TimeoutError:
            raise ProviderError(
                self.name,
                f"Claude CLI timed out after {self.config.timeout}s",
                retryable=True,
            )
        except FileNotFoundError:
            self._available = False
            raise ProviderError(
                self.name,
                "Claude CLI binary not found",
                retryable=False,
            )

    async def stream(
        self,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs
    ) -> AsyncIterator[str]:
        """Stream not directly supported by CLI — falls back to complete()"""
        result = await self.complete(messages, temperature, max_tokens, **kwargs)
        yield result.content

    def count_tokens(self, text: str) -> int:
        """Approximate token count"""
        return int(len(text) / 3.5)

    def use_premium_model(self):
        """Switch to Opus (main session model)"""
        self.config.model = "opus"

    def use_standard_model(self):
        """Switch to Sonnet (delegated agent)"""
        self.config.model = "sonnet"

    async def close(self):
        """No persistent connection to close"""
        pass
