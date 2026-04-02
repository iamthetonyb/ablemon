"""
Claude Code Provider - Connects ABLE to Claude Pro subscriptions via OAuth.

Uses the official Anthropic `claude-code` CLI under the hood to completely bypass
the API-key billing endpoint and instead utilize the user's flat-rate $100/mo subscription.
"""

import json
import logging
import asyncio
import os
from subprocess import PIPE
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
    Subprocess wrapper around `claude` CLI.
    """

    def __init__(self, model="claude-opus-4-6"):
        # We don't need a real API key or base URL since the CLI handles OAuth.
        config = ProviderConfig(
            api_key="oauth-handled-by-cli",
            base_url="cli://claude",
            model=model,
            timeout=300.0,
            cost_per_million_input=0.0,  # Unlimited via $100/mo plan
            cost_per_million_output=0.0
        )
        super().__init__(config)

    @property
    def name(self) -> str:
        return "claude_code"

    def count_tokens(self, text: str) -> int:
        return int(len(text) / 3.5)

    async def complete(
        self,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[str] = None,
        **kwargs
    ) -> CompletionResult:
        
        # Flatten messages into one payload for the CLI
        prompt = ""
        for msg in messages:
            if msg.role == Role.SYSTEM:
                prompt += f"[SYSTEM DIRECTIVE]:\n{msg.content}\n\n"
            else:
                prompt += f"{msg.role.value.capitalize()}:\n{msg.content}\n\n"

        # Ask Claude Code not to use interactive features
        prompt += "\nRespond directly to the user's latest message. Output only text or markdown."

        # Make sure ANTHROPIC_API_KEY is not set in the child environment,
        # otherwise the CLI crashes trying to use it instead of OAuth.
        env = os.environ.copy()
        env.pop("ANTHROPIC_API_KEY", None)

        cmd = [
            "claude", 
            "-p", prompt, 
            "--output-format", "json",
            "--model", self.config.model
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=PIPE,
                stderr=PIPE,
                env=env,
                # Increased limit in case CLI returns large contexts
                limit=1024 * 1024 * 10
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                err_text = stderr.decode('utf-8').strip()
                raise ProviderError(
                    self.name,
                    f"CLI failed with code {process.returncode}: {err_text}",
                    retryable=False
                )
                
            out_json = stdout.decode('utf-8').strip()
            # Often the CLI returns JSON logs on multiple lines if something goes wrong,
            # but usually it's a single parseable object on success (--output-format json).
            # If multiple lines, we take the last valid JSON obj.
            last_valid_obj = None
            for line in reversed(out_json.splitlines()):
                try:
                    last_valid_obj = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
                    
            if not last_valid_obj:
                raise ProviderError(self.name, "Unparseable response from Claude CLI", retryable=False)
                
            if last_valid_obj.get("is_error"):
                raise ProviderError(
                    self.name, 
                    f"Claude CLI Error: {last_valid_obj.get('result')}", 
                    retryable=False
                )
                
            content_text = last_valid_obj.get("result", "")
            usage_data = last_valid_obj.get("usage", {})
            in_tokens = usage_data.get("input_tokens", 0)
            out_tokens = usage_data.get("output_tokens", 0)
            
            usage = UsageStats(
                input_tokens=in_tokens,
                output_tokens=out_tokens,
                total_tokens=in_tokens + out_tokens
            )

            # Note: For strict tool calls, the CLI usually resolves them internally via MCP natively
            # but ABLE orchestrator expects string content responses to parse tool calls.
            # So returning string content works perfectly for our fallback orchestrator logic.
            
            return CompletionResult(
                content=str(content_text),
                finish_reason=last_valid_obj.get("stop_reason", "end_turn"),
                usage=usage,
                provider=self.name,
                model=self.config.model,
                tool_calls=None, # Claude CLI fulfills tools on its own or we parse them from text
                cost=0.0, # Zero additional cost via subscription
                raw_response=last_valid_obj
            )

        except Exception as e:
            if isinstance(e, ProviderError):
                raise
            raise ProviderError(self.name, f"Subprocess exception: {str(e)}", retryable=False)

    async def stream(
        self,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs
    ) -> AsyncIterator[str]:
        # Simple fallback for stream, treating it as synchronous.
        result = await self.complete(messages, temperature, max_tokens, **kwargs)
        yield result.content

    async def close(self):
        pass
