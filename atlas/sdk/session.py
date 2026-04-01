"""Session management for multi-turn conversations."""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class SessionMessage:
    role: str
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    tool_calls: List[Dict] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class Session:
    """Multi-turn conversation session with history and cost tracking."""

    def __init__(self, agent, session_id: str = None):
        self.agent = agent
        self.session_id = session_id or str(uuid.uuid4())[:8]
        self.messages: List[SessionMessage] = []
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_cost_usd: float = 0.0
        self.tools_used: List[str] = []
        self.started_at = datetime.now(timezone.utc)
        self._consecutive_failures = 0
        self._max_failures = 3

    async def send(self, message: str) -> str:
        """Send a message and get a response."""
        self.messages.append(SessionMessage(role="user", content=message))

        response = await self.agent._process(
            messages=[{"role": m.role, "content": m.content} for m in self.messages],
            tools=self.agent._tools,
            session=self,
        )

        self.messages.append(SessionMessage(role="assistant", content=response))
        return response

    def export_jsonl(self, path: Path) -> Path:
        """Export session as JSONL for distillation."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            for msg in self.messages:
                f.write(
                    json.dumps(
                        {
                            "role": msg.role,
                            "content": msg.content,
                            "timestamp": msg.timestamp,
                            "session_id": self.session_id,
                            "metadata": msg.metadata,
                        }
                    )
                    + "\n"
                )
        return path

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # Auto-export for distillation
        session_dir = Path.home() / ".atlas" / "sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        self.export_jsonl(session_dir / f"{self.session_id}.jsonl")

        # Trigger session end hooks
        if self.agent._hooks:
            await self.agent._hooks.trigger("on_session_end", session=self)
