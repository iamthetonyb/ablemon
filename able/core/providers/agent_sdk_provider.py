"""
Claude Agent SDK Provider — D1 (ABLE Distillation Initiative, Phase 1).

Wraps `claude-agent-sdk` (v0.1.58) as an ABLE LLMProvider.

Key design choices:
- `complete()` → `claude_agent_sdk.query()` with last user message as prompt
- System messages collapsed into `ClaudeAgentOptions.system_prompt`
- Hooks: PreToolUse → audit trail, PostToolUse → interaction logger, Stop → buddy XP
- Token usage extracted from `TaskUsage` on the `ResultMessage`
- Streaming via `ClaudeAgentOptions(stream=True)` — yields text chunks from `StreamEvent`
- Graceful degradation when SDK is not installed (raises `ProviderError` w/ install hint)
- `AgentTeamConfig` dataclass for multi-agent coordination (parallel subagents)
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

from .base import (
    CompletionResult,
    LLMProvider,
    Message,
    ProviderConfig,
    ProviderError,
    Role,
    ToolCall,
    UsageStats,
)

logger = logging.getLogger(__name__)

# Lazy import — SDK may not be installed in all environments
try:
    import claude_agent_sdk
    from claude_agent_sdk import (
        ClaudeAgentOptions,
        ClaudeSDKClient,
        PermissionMode,
        PostToolUseHookInput,
        PreToolUseHookInput,
        ResultMessage,
        StopHookInput,
        StreamEvent,
        TaskBudget,
        query,
    )
    _SDK_AVAILABLE = True
except ImportError:
    _SDK_AVAILABLE = False
    query = None  # type: ignore[assignment]


# ── Multi-agent coordination ──────────────────────────────────────────────────

@dataclass
class AgentTeamConfig:
    """
    Config for orchestrating multiple SDK agents in parallel.

    Each entry in `agents` is a dict with keys:
      - name (str): logical role label
      - prompt (str): specialised instruction prepended to the shared task
      - model (str, optional): override model per agent
      - budget (dict, optional): max_tokens / max_turns overrides

    Example::

        AgentTeamConfig(
            agents=[
                {"name": "researcher", "prompt": "Focus on facts and citations."},
                {"name": "critic",     "prompt": "Find weaknesses in the argument."},
            ],
            merge_strategy="concat",  # "concat" | "first" | "vote"
        )
    """
    agents: List[Dict[str, Any]] = field(default_factory=list)
    merge_strategy: str = "concat"   # concat | first | vote
    max_parallel: int = 4            # cap concurrent SDK calls


# ── Hook helpers ──────────────────────────────────────────────────────────────

def _make_pre_tool_hook(provider_name: str):
    """Returns a PreToolUse hook that writes to the audit trail."""
    def hook(h: "PreToolUseHookInput") -> None:  # type: ignore[name-defined]
        try:
            from able.audit.git_trail import AuditTrail
            AuditTrail.log(
                action="pre_tool_use",
                provider=provider_name,
                tool=getattr(h, "tool_name", str(h)),
                input=getattr(h, "tool_input", {}),
            )
        except Exception:
            pass  # Non-fatal; audit failures must never break execution
    return hook


def _make_post_tool_hook(provider_name: str):
    """Returns a PostToolUse hook that writes to the interaction logger."""
    def hook(h: "PostToolUseHookInput") -> None:  # type: ignore[name-defined]
        try:
            from able.core.distillation.interaction_auditor import log_tool_result
            log_tool_result(
                provider=provider_name,
                tool=getattr(h, "tool_name", str(h)),
                output=getattr(h, "tool_result", {}),
            )
        except Exception:
            pass
    return hook


def _make_stop_hook(start_time: float):
    """Returns a Stop hook that awards buddy XP on task completion."""
    def hook(h: "StopHookInput") -> None:  # type: ignore[name-defined]
        try:
            duration_min = (time.time() - start_time) / 60
            from able.core.buddy.xp import award_task_xp
            award_task_xp(duration_min=duration_min, source="agent_sdk")
        except Exception:
            pass
    return hook


# ── Provider ──────────────────────────────────────────────────────────────────

class AgentSDKProvider(LLMProvider):
    """
    ABLE LLMProvider that wraps `claude-agent-sdk`.

    Unlike REST-based providers, the SDK runs a *full agentic loop* internally —
    tool calls, retries, and result synthesis happen inside `query()`.  ABLE
    receives the final `ResultMessage` and maps it to `CompletionResult`.

    Hooks surface internal SDK events into ABLE's existing audit and distillation
    infrastructure without coupling the SDK to internal modules.
    """

    def __init__(
        self,
        api_key: str = "",
        model: str = "claude-opus-4-6",
        timeout: float = 300.0,
        permission_mode: str = "default",
        max_turns: int = 20,
        max_tokens: int = 8192,
        cwd: Optional[str] = None,
    ):
        config = ProviderConfig(
            api_key=api_key,
            model=model,
            timeout=timeout,
            # Tier-4 pricing: $15/$75 per M (matches routing_config.yaml)
            cost_per_million_input=15.0,
            cost_per_million_output=75.0,
        )
        super().__init__(config)
        self._permission_mode = permission_mode
        self._max_turns = max_turns
        self._max_tokens = max_tokens
        self._cwd = cwd

    @property
    def name(self) -> str:
        return "agent_sdk"

    # ── Options builder ───────────────────────────────────────────

    def _build_options(
        self,
        system_prompt: str,
        start_time: float,
        stream: bool = False,
        **kwargs,
    ) -> "ClaudeAgentOptions":  # type: ignore[name-defined]
        """Assemble ClaudeAgentOptions with hooks wired."""
        opts_kwargs: Dict[str, Any] = {
            "model": self.config.model,
            "max_turns": self._max_turns,
            "stream": stream,
            "hooks": {
                "pre_tool_use": _make_pre_tool_hook(self.name),
                "post_tool_use": _make_post_tool_hook(self.name),
                "stop": _make_stop_hook(start_time),
            },
        }

        if system_prompt:
            opts_kwargs["system_prompt"] = system_prompt

        if self._permission_mode != "default":
            opts_kwargs["permission_mode"] = PermissionMode(self._permission_mode)

        budget_kwargs: Dict[str, Any] = {"max_tokens": self._max_tokens}
        if kwargs.get("max_budget_tokens"):
            budget_kwargs["max_tokens"] = kwargs["max_budget_tokens"]
        opts_kwargs["budget"] = TaskBudget(**budget_kwargs)

        if self._cwd:
            opts_kwargs["cwd"] = self._cwd

        return ClaudeAgentOptions(**opts_kwargs)

    # ── Message extraction helpers ────────────────────────────────

    @staticmethod
    def _extract_prompt_and_system(messages: List[Message]):
        """Return (prompt, system_prompt) from message list."""
        system_parts: List[str] = []
        prompt = ""
        for msg in messages:
            if msg.role == Role.SYSTEM:
                system_parts.append(str(msg.content))
            elif msg.role == Role.USER:
                # Use the last user message as the prompt
                prompt = str(msg.content)
        return prompt, "\n\n".join(system_parts)

    @staticmethod
    def _usage_from_result(result: "ResultMessage") -> UsageStats:  # type: ignore[name-defined]
        """Extract UsageStats from SDK ResultMessage.task_usage (may be None)."""
        try:
            usage = getattr(result, "task_usage", None) or getattr(result, "usage", None)
            if usage is None:
                return UsageStats()
            inp = getattr(usage, "input_tokens", 0) or 0
            out = getattr(usage, "output_tokens", 0) or 0
            return UsageStats(
                input_tokens=inp,
                output_tokens=out,
                total_tokens=inp + out,
            )
        except Exception:
            return UsageStats()

    # ── LLMProvider interface ─────────────────────────────────────

    async def complete(
        self,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[str] = None,
        **kwargs,
    ) -> CompletionResult:
        """
        Run a full agent loop via `claude_agent_sdk.query()`.

        The SDK handles tool calls internally; ABLE only sees the final result.
        Hooks surface pre/post tool events into audit + distillation pipelines.
        """
        if not _SDK_AVAILABLE:
            raise ProviderError(
                self.name,
                "claude-agent-sdk not installed. Run: pip install claude-agent-sdk",
                retryable=False,
            )

        prompt, system_prompt = self._extract_prompt_and_system(messages)
        if not prompt:
            raise ProviderError(self.name, "No user message found in messages list", retryable=False)

        start = time.time()
        opts = self._build_options(system_prompt, start, stream=False, **kwargs)

        try:
            # query() is sync in v0.1.58 — run in executor to avoid blocking the loop
            loop = asyncio.get_running_loop()
            result: ResultMessage = await loop.run_in_executor(
                None, lambda: query(prompt, opts)
            )
        except Exception as e:
            raise ProviderError(self.name, f"SDK query failed: {e}", retryable=True)

        content = getattr(result, "result", "") or getattr(result, "content", "") or ""
        if isinstance(content, list):
            # Some SDK versions return content as list of blocks
            content = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            )

        usage = self._usage_from_result(result)
        cost = self.calculate_cost(usage.input_tokens, usage.output_tokens)

        return CompletionResult(
            content=content,
            finish_reason="stop",
            usage=usage,
            provider=self.name,
            model=self.config.model,
            latency_ms=(time.time() - start) * 1000,
            cost=cost,
            raw_response={"sdk_result": str(result)},
        )

    async def stream(
        self,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> AsyncIterator[str]:
        """
        Stream text chunks from the SDK agent loop.

        Uses `ClaudeAgentOptions(stream=True)`; yields text from each
        `StreamEvent` as it arrives.  Falls back to non-streaming on SDK
        versions that don't support it.
        """
        if not _SDK_AVAILABLE:
            raise ProviderError(
                self.name,
                "claude-agent-sdk not installed. Run: pip install claude-agent-sdk",
                retryable=False,
            )

        prompt, system_prompt = self._extract_prompt_and_system(messages)
        if not prompt:
            raise ProviderError(self.name, "No user message found in messages list", retryable=False)

        start = time.time()
        opts = self._build_options(system_prompt, start, stream=True, **kwargs)

        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def _run_streaming():
            try:
                for event in query(prompt, opts):
                    if isinstance(event, StreamEvent) or hasattr(event, "text"):
                        text = getattr(event, "text", "") or ""
                        if text:
                            loop.call_soon_threadsafe(queue.put_nowait, text)
            except Exception as e:
                loop.call_soon_threadsafe(
                    queue.put_nowait, ProviderError(self.name, str(e), retryable=True)
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel

        # Run SDK in thread; yield chunks to caller via queue
        fut = loop.run_in_executor(None, _run_streaming)

        while True:
            item = await queue.get()
            if item is None:
                break
            if isinstance(item, ProviderError):
                raise item
            yield item

        await fut  # propagate any executor exception

    async def run_team(
        self,
        messages: List[Message],
        team: AgentTeamConfig,
        **kwargs,
    ) -> CompletionResult:
        """
        Run multiple SDK agents in parallel and merge results.

        Each agent in `team.agents` gets the shared prompt with its specialised
        prefix.  Results are merged per `team.merge_strategy`.
        """
        prompt, system_prompt = self._extract_prompt_and_system(messages)

        sem = asyncio.Semaphore(team.max_parallel)

        async def run_one(agent_cfg: Dict[str, Any]) -> str:
            async with sem:
                prefix = agent_cfg.get("prompt", "")
                agent_prompt = f"{prefix}\n\n{prompt}".strip() if prefix else prompt
                agent_model = agent_cfg.get("model", self.config.model)

                # Temporarily swap model for this agent
                orig_model = self.config.model
                self.config.model = agent_model
                try:
                    result = await self.complete(
                        [Message(role=Role.USER, content=agent_prompt)],
                        **kwargs,
                    )
                    return result.content
                finally:
                    self.config.model = orig_model

        results = await asyncio.gather(*[run_one(a) for a in team.agents], return_exceptions=True)
        texts = [r for r in results if isinstance(r, str)]

        if not texts:
            raise ProviderError(self.name, "All team agents failed", retryable=True)

        if team.merge_strategy == "first":
            content = texts[0]
        elif team.merge_strategy == "vote":
            # Simple majority: return most common response (exact match)
            from collections import Counter
            content = Counter(texts).most_common(1)[0][0]
        else:  # concat (default)
            labels = [a.get("name", f"agent_{i}") for i, a in enumerate(team.agents)]
            parts = [f"[{labels[i]}]\n{t}" for i, t in enumerate(texts)]
            content = "\n\n".join(parts)

        return CompletionResult(
            content=content,
            finish_reason="stop",
            usage=UsageStats(),
            provider=self.name,
            model=self.config.model,
            raw_response={"team_size": len(team.agents), "successful": len(texts)},
        )

    def count_tokens(self, text: str) -> int:
        """Approximate token count (4 chars ≈ 1 token)."""
        return max(1, len(text) // 4)
