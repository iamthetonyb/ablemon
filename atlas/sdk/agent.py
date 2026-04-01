"""ATLASAgent -- build custom AI agents on ATLAS infrastructure."""

import inspect
import logging
from typing import Callable, List, Optional, Union

_UNSET = object()  # Sentinel to distinguish "not initialized" from None

from atlas.sdk.errors import APIError
from atlas.sdk.hooks import HookManager
from atlas.sdk.session import Session
from atlas.sdk.tool import ToolDefinition

logger = logging.getLogger(__name__)


class ATLASAgent:
    """Build custom AI agents on ATLAS infrastructure.

    Usage:
        agent = ATLASAgent(name="my-agent", system_prompt="You are...")
        response = await agent.run("Do something")

        async with agent.session() as s:
            r1 = await s.send("First message")
            r2 = await s.send("Follow up")
    """

    def __init__(
        self,
        name: str,
        system_prompt: str = "",
        tenant_id: str = "tony",
        tools: List[Union[ToolDefinition, Callable]] = None,
        tier: Union[int, str] = "auto",
        offline: bool = False,
        memory: bool = True,
        hooks: HookManager = None,
    ):
        self.name = name
        self.system_prompt = system_prompt
        self.tenant_id = tenant_id
        self.tier = tier
        self.offline = offline
        self._hooks = hooks or HookManager()
        self._max_tool_iterations = 20
        self._max_consecutive_failures = 3

        # Process tools
        self._tools: List[ToolDefinition] = []
        for t in tools or []:
            if hasattr(t, "_tool_definition"):
                self._tools.append(t._tool_definition)
            elif isinstance(t, ToolDefinition):
                self._tools.append(t)

        # Lazy-load providers (_UNSET means not yet attempted)
        self._provider_registry = _UNSET
        self._scorer = _UNSET

    def _init_providers(self):
        """Lazy init providers on first use."""
        if self._provider_registry is not _UNSET:
            return
        try:
            from atlas.core.routing.provider_registry import ProviderRegistry

            self._provider_registry = ProviderRegistry.from_yaml(
                "config/routing_config.yaml"
            )
        except (ImportError, Exception) as e:
            logger.warning(f"ProviderRegistry not available: {e}")
            self._provider_registry = None
        try:
            from atlas.core.routing.complexity_scorer import ComplexityScorer

            self._scorer = ComplexityScorer()
        except (ImportError, Exception) as e:
            logger.warning(f"ComplexityScorer not available: {e}")
            self._scorer = None

    def _resolve_tier(self, message: str, tier_override: Optional[int] = None) -> int:
        """Determine which tier to route to."""
        if tier_override is not None:
            return tier_override
        if isinstance(self.tier, int):
            return self.tier
        if self.offline:
            return 5
        if self._scorer is not None and self._scorer is not _UNSET:
            result = self._scorer.score(message, {})
            score = result.score
            if score < 0.4:
                return 1
            elif score < 0.7:
                return 2
            else:
                return 4
        return 1

    def _get_provider_chain(self, tier: int):
        """Get a provider chain for the given tier."""
        if self._provider_registry is None or self._provider_registry is _UNSET:
            return None
        try:
            return self._provider_registry.build_chain_for_tier(tier)
        except Exception:
            return None

    async def run(self, message: str, tier: int = None) -> str:
        """Single-turn: send message, get response (with tool use loop)."""
        self._init_providers()

        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": message})

        return await self._process(messages, self._tools, tier=tier)

    async def _process(
        self,
        messages: List[dict],
        tools: List[ToolDefinition],
        session: Session = None,
        tier: int = None,
    ) -> str:
        """Core agent loop with tool execution."""
        self._init_providers()

        user_msg = messages[-1]["content"] if messages else ""
        effective_tier = self._resolve_tier(user_msg, tier)

        # Get provider chain
        chain = self._get_provider_chain(effective_tier)
        if not chain:
            return f"No provider available for tier {effective_tier}"

        # Build provider-format messages
        from atlas.core.providers.base import Message, Role

        def to_provider_messages(raw: List[dict]) -> List[Message]:
            out = []
            for m in raw:
                role = Role(m["role"])
                out.append(Message(role=role, content=m.get("content", "")))
            return out

        # Tool schemas
        tool_schemas = [t.to_openai_schema() for t in tools] if tools else None

        consecutive_failures = 0

        for _ in range(self._max_tool_iterations):
            try:
                provider_msgs = to_provider_messages(messages)
                result = await chain.complete(provider_msgs, tools=tool_schemas)
            except Exception as e:
                raise APIError(str(e), provider="chain")

            # No tool calls = final response
            if not result.tool_calls:
                return result.content or ""

            # Append assistant message
            messages.append(
                {
                    "role": "assistant",
                    "content": result.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "name": tc.name,
                            "arguments": tc.arguments,
                        }
                        for tc in result.tool_calls
                    ],
                }
            )

            # Execute each tool call
            for tc in result.tool_calls:
                tool_def = next((t for t in tools if t.name == tc.name), None)

                if not tool_def:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": f"Unknown tool: {tc.name}",
                        }
                    )
                    continue

                # Pre-tool hook
                allowed = await self._hooks.trigger(
                    "pre_tool_use", tool_name=tc.name, args=tc.arguments
                )
                if allowed is False:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": "Tool blocked by hook",
                        }
                    )
                    continue

                # Execute
                args = tc.arguments if isinstance(tc.arguments, dict) else {}
                try:
                    if inspect.iscoroutinefunction(tool_def.handler):
                        tool_result = await tool_def.handler(**args)
                    else:
                        tool_result = tool_def.handler(**args)
                    tool_result = str(tool_result)
                    consecutive_failures = 0

                    if session:
                        session.tools_used.append(tc.name)
                except Exception as e:
                    consecutive_failures += 1
                    tool_result = f"Error: {e}"
                    if consecutive_failures >= self._max_consecutive_failures:
                        tool_result += (
                            f" (Failure cap reached: {self._max_consecutive_failures})"
                        )

                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": tool_result}
                )

                # Post-tool hook
                await self._hooks.trigger(
                    "post_tool_use",
                    tool_name=tc.name,
                    args=args,
                    result=tool_result,
                )

        return "Max tool iterations reached"

    def session(self, session_id: str = None) -> Session:
        """Create a new multi-turn session."""
        return Session(agent=self, session_id=session_id)

    def on(self, event: str):
        """Decorator to register a hook."""
        return self._hooks.on(event)
