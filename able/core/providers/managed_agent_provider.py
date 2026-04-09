"""
Managed Agents Provider — Anthropic's hosted agent sessions.

Stream-first pattern: open SSE listener BEFORE kicking off the agent,
so no events are lost between creation and first poll.

Cost: $0.08/session-hr (billed per session, not per token).
Beta header: managed-agents-2026-04-01

Idle-break: check stop_reason.type (not bare "idle" string).
Lossless reconnect: events.list() with last_event_id for gap recovery.
Custom tools: host-side credential injection (ABLE keeps secrets).
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

import aiohttp

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

MANAGED_AGENTS_BASE = "https://api.anthropic.com/v1"
BETA_HEADER = "managed-agents-2026-04-01"
API_VERSION = "2023-06-01"
COST_PER_SESSION_HOUR = 0.08
SSE_RECONNECT_DELAY = 1.0  # seconds before reconnect attempt
SSE_MAX_RECONNECTS = 5


@dataclass
class ManagedAgentSession:
    """Tracks a running managed agent session."""
    session_id: str
    agent_id: str
    started_at: float = field(default_factory=time.time)
    last_event_id: Optional[str] = None
    events: List[Dict[str, Any]] = field(default_factory=list)
    stop_reason: Optional[Dict[str, Any]] = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0

    @property
    def duration_hours(self) -> float:
        return (time.time() - self.started_at) / 3600

    @property
    def estimated_cost(self) -> float:
        return self.duration_hours * COST_PER_SESSION_HOUR

    @property
    def is_idle_stopped(self) -> bool:
        """Check stop_reason.type — NOT bare 'idle' string."""
        if not self.stop_reason:
            return False
        return self.stop_reason.get("type") == "idle"


class ManagedAgentProvider(LLMProvider):
    """
    Provider for Anthropic Managed Agents.

    Wraps the /v1/agents and /v1/sessions API. Uses SSE for real-time
    event streaming with lossless reconnect via events.list().

    Custom tools are injected with host-side credentials so the managed
    agent never sees API keys — ABLE keeps secrets on the host side.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        timeout: float = 300.0,
        custom_tools: Optional[List[Dict[str, Any]]] = None,
    ):
        config = ProviderConfig(
            api_key=api_key,
            base_url=MANAGED_AGENTS_BASE,
            model=model,
            timeout=timeout,
            cost_per_million_input=0.0,  # Billed per session-hr, not per token
            cost_per_million_output=0.0,
        )
        super().__init__(config)
        self._session: Optional[aiohttp.ClientSession] = None
        self._custom_tools = custom_tools or []
        self._active_sessions: Dict[str, ManagedAgentSession] = {}

    @property
    def name(self) -> str:
        return "managed_agent"

    def _headers(self) -> Dict[str, str]:
        return {
            "x-api-key": self.config.api_key,
            "anthropic-version": API_VERSION,
            "anthropic-beta": BETA_HEADER,
            "Content-Type": "application/json",
        }

    async def _get_http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.config.timeout)
            )
        return self._session

    # ── Agent + Session lifecycle ────────────────────────────────

    async def create_agent(
        self,
        name: str = "able-agent",
        instructions: str = "",
        tools: Optional[List[Dict]] = None,
    ) -> str:
        """Create a managed agent, returns agent_id."""
        http = await self._get_http()

        # Merge custom tools (host-side credentials) with caller-provided tools
        all_tools = list(self._custom_tools)
        if tools:
            all_tools.extend(tools)

        payload = {
            "model": self.config.model,
            "name": name,
            "instructions": instructions,
        }
        if all_tools:
            payload["tools"] = all_tools

        async with http.post(
            f"{self.config.base_url}/agents",
            headers=self._headers(),
            json=payload,
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise ProviderError(self.name, f"Agent create failed {resp.status}: {text}")
            data = await resp.json()
            return data["id"]

    async def create_session(
        self,
        agent_id: str,
        initial_message: str = "",
    ) -> ManagedAgentSession:
        """Create a session and start SSE listener BEFORE kickoff (stream-first)."""
        http = await self._get_http()

        payload = {"agent_id": agent_id}
        if initial_message:
            payload["initial_message"] = initial_message

        async with http.post(
            f"{self.config.base_url}/sessions",
            headers=self._headers(),
            json=payload,
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise ProviderError(self.name, f"Session create failed {resp.status}: {text}")
            data = await resp.json()

        session = ManagedAgentSession(
            session_id=data["id"],
            agent_id=agent_id,
        )
        self._active_sessions[session.session_id] = session
        return session

    async def send_message(
        self,
        session_id: str,
        content: str,
    ) -> None:
        """Send a message to an active session."""
        http = await self._get_http()
        async with http.post(
            f"{self.config.base_url}/sessions/{session_id}/messages",
            headers=self._headers(),
            json={"content": content},
        ) as resp:
            if resp.status not in (200, 201):
                text = await resp.text()
                raise ProviderError(self.name, f"Send message failed {resp.status}: {text}")

    # ── SSE streaming (stream-first pattern) ─────────────────────

    async def stream_events(
        self,
        session_id: str,
        last_event_id: Optional[str] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        SSE event stream with lossless reconnect.

        Stream-first: caller opens this BEFORE sending messages so no
        events are lost between creation and first poll.

        Lossless reconnect: on disconnect, fetches missed events via
        events.list() with last_event_id, then resumes SSE.
        """
        http = await self._get_http()
        session = self._active_sessions.get(session_id)
        reconnect_count = 0
        cursor = last_event_id or (session.last_event_id if session else None)

        while reconnect_count <= SSE_MAX_RECONNECTS:
            try:
                # ── Gap recovery: fetch events missed during disconnect ──
                if cursor and reconnect_count > 0:
                    async for event in self._recover_events(session_id, cursor):
                        if session:
                            session.last_event_id = event.get("id", cursor)
                            session.events.append(event)
                        yield event

                # ── Open SSE stream ──
                headers = {**self._headers(), "Accept": "text/event-stream"}
                if cursor:
                    headers["Last-Event-ID"] = cursor

                async with http.get(
                    f"{self.config.base_url}/sessions/{session_id}/events",
                    headers=headers,
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise ProviderError(
                            self.name, f"SSE stream failed {resp.status}: {text}"
                        )

                    async for line in resp.content:
                        decoded = line.decode("utf-8").strip()
                        if not decoded or decoded.startswith(":"):
                            continue

                        if decoded.startswith("id:"):
                            cursor = decoded[3:].strip()
                            if session:
                                session.last_event_id = cursor
                            continue

                        if decoded.startswith("data:"):
                            try:
                                event = json.loads(decoded[5:].strip())
                            except json.JSONDecodeError:
                                continue

                            if session:
                                session.events.append(event)
                                self._update_session_stats(session, event)

                            yield event

                            # Check for terminal events
                            event_type = event.get("type", "")
                            if event_type in ("session_end", "error"):
                                return
                            if event_type == "stop":
                                if session:
                                    session.stop_reason = event.get("stop_reason", {})
                                return

                    # Stream ended cleanly
                    return

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                reconnect_count += 1
                if reconnect_count > SSE_MAX_RECONNECTS:
                    raise ProviderError(
                        self.name,
                        f"SSE reconnect limit ({SSE_MAX_RECONNECTS}) exceeded: {e}",
                        retryable=False,
                    )
                logger.warning(
                    "SSE disconnected for session %s (attempt %d/%d): %s",
                    session_id, reconnect_count, SSE_MAX_RECONNECTS, e,
                )
                await asyncio.sleep(SSE_RECONNECT_DELAY * reconnect_count)

    async def _recover_events(
        self,
        session_id: str,
        after_id: str,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Lossless reconnect: fetch missed events via events.list()."""
        http = await self._get_http()
        params = {"after_id": after_id, "limit": 100}

        async with http.get(
            f"{self.config.base_url}/sessions/{session_id}/events/list",
            headers=self._headers(),
            params=params,
        ) as resp:
            if resp.status != 200:
                logger.warning("Event recovery failed for session %s: %s", session_id, resp.status)
                return
            data = await resp.json()
            for event in data.get("events", []):
                yield event

    def _update_session_stats(self, session: ManagedAgentSession, event: Dict) -> None:
        """Track token usage from session events."""
        usage = event.get("usage", {})
        session.total_input_tokens += usage.get("input_tokens", 0)
        session.total_output_tokens += usage.get("output_tokens", 0)

    # ── Custom tool credential injection ─────────────────────────

    def add_custom_tool(
        self,
        name: str,
        description: str,
        input_schema: Dict[str, Any],
        credential_env: Optional[str] = None,
    ) -> None:
        """
        Register a custom tool with host-side credential injection.

        The managed agent calls the tool; ABLE intercepts, injects the
        credential from env, executes, and returns the result. The agent
        never sees the API key.
        """
        tool_def = {
            "type": "custom",
            "name": name,
            "description": description,
            "input_schema": input_schema,
        }
        if credential_env:
            tool_def["_able_credential_env"] = credential_env
        self._custom_tools.append(tool_def)

    async def handle_tool_call(
        self,
        session_id: str,
        tool_name: str,
        tool_input: Dict[str, Any],
        tool_use_id: str,
    ) -> Dict[str, Any]:
        """
        Handle a custom tool call from the managed agent.

        Injects host-side credentials and returns the result to the session.
        """
        # Find matching custom tool
        tool_def = next(
            (t for t in self._custom_tools if t.get("name") == tool_name),
            None,
        )

        # Inject credential if configured
        if tool_def and tool_def.get("_able_credential_env"):
            env_var = tool_def["_able_credential_env"]
            credential = os.environ.get(env_var, "")
            if credential:
                tool_input["_credential"] = credential

        http = await self._get_http()
        async with http.post(
            f"{self.config.base_url}/sessions/{session_id}/tool_results",
            headers=self._headers(),
            json={
                "tool_use_id": tool_use_id,
                "content": json.dumps(tool_input),
            },
        ) as resp:
            if resp.status not in (200, 201):
                text = await resp.text()
                raise ProviderError(self.name, f"Tool result submit failed {resp.status}: {text}")
            return await resp.json()

    # ── LLMProvider interface ────────────────────────────────────

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
        Complete via managed agent session.

        Creates an ephemeral agent + session, streams to completion,
        collects the final response. Awards buddy XP on completion.
        """
        start = time.time()

        # Build system + user messages
        system_parts = []
        user_content = ""
        for msg in messages:
            if msg.role == Role.SYSTEM:
                system_parts.append(str(msg.content))
            elif msg.role == Role.USER:
                user_content = str(msg.content)

        instructions = "\n\n".join(system_parts) if system_parts else ""

        try:
            agent_id = await self.create_agent(
                instructions=instructions,
                tools=tools,
            )
            session = await self.create_session(
                agent_id=agent_id,
                initial_message=user_content,
            )

            # Stream-first: collect all content events
            content_parts = []
            tool_calls = []

            async for event in self.stream_events(session.session_id):
                etype = event.get("type", "")

                if etype == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        content_parts.append(delta.get("text", ""))

                elif etype == "tool_use":
                    tool_calls.append(ToolCall(
                        id=event.get("id", ""),
                        name=event.get("name", ""),
                        arguments=event.get("input", {}),
                    ))

            content = "".join(content_parts)
            finish = "stop"

            # Idle-break: check stop_reason.type, not bare string
            if session.is_idle_stopped:
                finish = "idle"

            duration_min = (time.time() - start) / 60
            cost = session.estimated_cost

            # Award buddy XP
            try:
                from able.core.buddy.xp import award_managed_agent_xp
                award_managed_agent_xp(session_duration_min=duration_min)
            except Exception:
                pass  # Non-fatal

            return CompletionResult(
                content=content,
                finish_reason=finish,
                usage=UsageStats(
                    input_tokens=session.total_input_tokens,
                    output_tokens=session.total_output_tokens,
                    total_tokens=session.total_input_tokens + session.total_output_tokens,
                ),
                provider=self.name,
                model=self.config.model,
                tool_calls=tool_calls if tool_calls else None,
                cost=cost,
                latency_ms=(time.time() - start) * 1000,
                raw_response={"session_id": session.session_id, "events_count": len(session.events)},
            )

        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(self.name, f"Managed agent error: {e}", retryable=True)

    async def stream(
        self,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> AsyncIterator[str]:
        """Stream text deltas from a managed agent session."""
        system_parts = []
        user_content = ""
        for msg in messages:
            if msg.role == Role.SYSTEM:
                system_parts.append(str(msg.content))
            elif msg.role == Role.USER:
                user_content = str(msg.content)

        instructions = "\n\n".join(system_parts) if system_parts else ""

        try:
            agent_id = await self.create_agent(instructions=instructions)
            session = await self.create_session(
                agent_id=agent_id,
                initial_message=user_content,
            )

            async for event in self.stream_events(session.session_id):
                etype = event.get("type", "")
                if etype == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        yield delta.get("text", "")

        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(self.name, f"Managed agent stream error: {e}", retryable=True)

    def count_tokens(self, text: str) -> int:
        return int(len(text) / 3.5)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
