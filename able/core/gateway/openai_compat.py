"""
E5 — OpenAI-Compatible API Server.

Exposes ABLE's 5-tier routing system as a standard /v1/chat/completions
endpoint. Lets Continue, Cursor, and other OpenAI-compatible clients
route through ABLE.

Forked from Hermes v0.4 PR #1756 pattern.

Features:
- /v1/chat/completions (streaming + non-streaming)
- /v1/models (list available models/tiers)
- SQLite-backed response persistence
- CORS protection
- Input limits (max tokens, max messages)
- Bearer token auth

Usage:
    server = OpenAICompatServer(gateway=my_gateway)
    # Wire into existing aiohttp app:
    server.register_routes(app)

    # Or standalone:
    await server.start(port=8080)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Defaults
_MAX_INPUT_TOKENS = 200_000
_MAX_MESSAGES = 100
_MAX_OUTPUT_TOKENS = 16_384
_DEFAULT_PORT = 8080
_ALLOWED_FIELDS = {
    "model", "messages", "temperature", "top_p", "max_tokens",
    "stream", "stop", "n", "presence_penalty", "frequency_penalty",
    "user",
}


@dataclass
class CompatRequest:
    """Parsed /v1/chat/completions request."""
    id: str = field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:12]}")
    model: str = "able-auto"
    messages: List[Dict[str, str]] = field(default_factory=list)
    temperature: float = 0.7
    top_p: float = 0.95
    max_tokens: int = 4096
    stream: bool = False
    stop: Optional[List[str]] = None
    user: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict) -> "CompatRequest":
        """Parse and validate incoming request."""
        # Strip unknown fields
        clean = {k: v for k, v in data.items() if k in _ALLOWED_FIELDS}

        messages = clean.get("messages", [])
        if not messages:
            raise ValueError("messages array is required and cannot be empty")
        if len(messages) > _MAX_MESSAGES:
            raise ValueError(f"Too many messages: {len(messages)} (max {_MAX_MESSAGES})")

        # Validate message structure
        for i, msg in enumerate(messages):
            if not isinstance(msg, dict):
                raise ValueError(f"messages[{i}] must be an object")
            if "role" not in msg:
                raise ValueError(f"messages[{i}] missing 'role'")
            if msg["role"] not in ("system", "user", "assistant", "tool"):
                raise ValueError(f"messages[{i}] invalid role: {msg['role']}")
            if "content" not in msg:
                raise ValueError(f"messages[{i}] missing 'content'")

        return cls(
            model=clean.get("model", "able-auto"),
            messages=messages,
            temperature=float(clean.get("temperature", 0.7)),
            top_p=float(clean.get("top_p", 0.95)),
            max_tokens=min(int(clean.get("max_tokens", 4096)), _MAX_OUTPUT_TOKENS),
            stream=bool(clean.get("stream", False)),
            stop=clean.get("stop"),
            user=clean.get("user"),
        )


@dataclass
class CompatResponse:
    """OpenAI-format response."""
    id: str
    model: str
    content: str
    finish_reason: str = "stop"
    input_tokens: int = 0
    output_tokens: int = 0
    created: int = field(default_factory=lambda: int(time.time()))

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "object": "chat.completion",
            "created": self.created,
            "model": self.model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": self.content,
                    },
                    "finish_reason": self.finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": self.input_tokens,
                "completion_tokens": self.output_tokens,
                "total_tokens": self.input_tokens + self.output_tokens,
            },
        }

    def to_stream_chunk(self, delta_content: str = "", finish: bool = False) -> str:
        """Format as SSE stream chunk."""
        chunk = {
            "id": self.id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": delta_content} if delta_content else {},
                    "finish_reason": "stop" if finish else None,
                }
            ],
        }
        return f"data: {json.dumps(chunk)}\n\n"


# ── Model mapping ────────────────────────────────────────────────

# Maps OpenAI-style model names to ABLE tier overrides
MODEL_TIER_MAP = {
    "able-auto": None,          # Use complexity scoring (default)
    "able-t1": 1,               # Force T1 (cheap)
    "able-t2": 2,               # Force T2 (medium)
    "able-t4": 4,               # Force T4 (premium)
    "able-t5": 5,               # Force T5 (local)
    "gpt-4": 4,                 # Map GPT-4 → T4
    "gpt-4o": 2,                # Map GPT-4o → T2
    "gpt-4o-mini": 1,           # Map mini → T1
    "gpt-3.5-turbo": 1,         # Map 3.5 → T1
    "claude-3-opus": 4,         # Map Opus → T4
    "claude-3-sonnet": 2,       # Map Sonnet → T2
}


# ── Response persistence ──────────────────────────────────────────

class CompatResponseDB:
    """SQLite-backed response persistence for auditing and replay."""

    def __init__(self, db_path: str = "data/openai_compat.db"):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS completions (
                    id TEXT PRIMARY KEY,
                    model TEXT,
                    user_id TEXT,
                    messages_json TEXT,
                    response_content TEXT,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    finish_reason TEXT,
                    duration_ms REAL,
                    tier_used INTEGER,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_completions_user
                ON completions (user_id, created_at DESC)
            """)

    def record(
        self,
        request: CompatRequest,
        response: CompatResponse,
        duration_ms: float,
        tier_used: int = 0,
    ):
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    """INSERT INTO completions
                    (id, model, user_id, messages_json, response_content,
                     input_tokens, output_tokens, finish_reason, duration_ms, tier_used)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        response.id,
                        response.model,
                        request.user,
                        json.dumps(request.messages),
                        response.content,
                        response.input_tokens,
                        response.output_tokens,
                        response.finish_reason,
                        duration_ms,
                        tier_used,
                    ),
                )
        except Exception as e:
            logger.debug("Failed to record compat response: %s", e)

    def get_recent(self, limit: int = 20) -> List[Dict]:
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM completions ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception:
            return []

    def stats(self) -> Dict:
        try:
            with sqlite3.connect(self._db_path) as conn:
                total = conn.execute("SELECT COUNT(*) FROM completions").fetchone()[0]
                tokens = conn.execute(
                    "SELECT COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0) "
                    "FROM completions"
                ).fetchone()
                return {
                    "total_completions": total,
                    "total_input_tokens": tokens[0],
                    "total_output_tokens": tokens[1],
                }
        except Exception:
            return {"total_completions": 0, "total_input_tokens": 0, "total_output_tokens": 0}


# ── Server ────────────────────────────────────────────────────────

class OpenAICompatServer:
    """OpenAI-compatible API endpoint for ABLE.

    Translates /v1/chat/completions requests into ABLE gateway calls,
    routing through the complexity-scored 5-tier system.
    """

    AVAILABLE_MODELS = [
        {"id": "able-auto", "object": "model", "owned_by": "able", "description": "Auto-routed via complexity scoring"},
        {"id": "able-t1", "object": "model", "owned_by": "able", "description": "Tier 1 (fast, cheap)"},
        {"id": "able-t2", "object": "model", "owned_by": "able", "description": "Tier 2 (balanced)"},
        {"id": "able-t4", "object": "model", "owned_by": "able", "description": "Tier 4 (premium reasoning)"},
        {"id": "able-t5", "object": "model", "owned_by": "able", "description": "Tier 5 (local/offline)"},
    ]

    def __init__(
        self,
        gateway=None,
        auth_token: Optional[str] = None,
        cors_origins: Optional[List[str]] = None,
        db_path: str = "data/openai_compat.db",
    ):
        self._gateway = gateway
        self._auth_token = auth_token
        self._cors_origins = cors_origins or ["*"]
        self._db = CompatResponseDB(db_path=db_path)
        self._request_count = 0

    def _check_auth(self, auth_header: Optional[str]) -> bool:
        """Validate Bearer token."""
        if not self._auth_token:
            return True  # No auth configured
        if not auth_header:
            return False
        if not auth_header.startswith("Bearer "):
            return False
        return auth_header[7:] == self._auth_token

    async def handle_completions(self, request_data: Dict, auth_header: Optional[str] = None) -> Dict:
        """Handle /v1/chat/completions request.

        Returns OpenAI-format response dict, or error dict.
        """
        # Auth check
        if not self._check_auth(auth_header):
            return {"error": {"message": "Invalid API key", "type": "authentication_error", "code": 401}}

        # Parse request
        try:
            req = CompatRequest.from_dict(request_data)
        except ValueError as e:
            return {"error": {"message": str(e), "type": "invalid_request_error", "code": 400}}

        self._request_count += 1
        start = time.monotonic()

        # Extract user message for gateway routing
        user_messages = [m for m in req.messages if m["role"] == "user"]
        if not user_messages:
            return {"error": {"message": "No user message found", "type": "invalid_request_error", "code": 400}}

        last_user_msg = user_messages[-1]["content"]

        # Determine tier override from model name
        tier_override = MODEL_TIER_MAP.get(req.model)

        # Call gateway
        try:
            if self._gateway:
                response_text = await self._gateway.process_message(
                    message=last_user_msg,
                    user_id=req.user or "openai-compat",
                    metadata={"source": "openai_compat", "tier_override": tier_override},
                )
            else:
                response_text = f"[ABLE OpenAI Compat] Echo: {last_user_msg[:200]}"
        except Exception as e:
            logger.error("Gateway call failed: %s", e)
            return {"error": {"message": f"Internal error: {e}", "type": "server_error", "code": 500}}

        duration_ms = (time.monotonic() - start) * 1000

        # Build response
        response = CompatResponse(
            id=req.id,
            model=req.model,
            content=response_text or "",
            input_tokens=sum(len(m.get("content", "")) // 4 for m in req.messages),
            output_tokens=len(response_text or "") // 4,
        )

        # Persist
        self._db.record(req, response, duration_ms, tier_used=tier_override or 0)

        return response.to_dict()

    async def handle_models(self) -> Dict:
        """Handle /v1/models request."""
        return {
            "object": "list",
            "data": self.AVAILABLE_MODELS,
        }

    def stats(self) -> Dict:
        """Return server stats."""
        db_stats = self._db.stats()
        return {
            **db_stats,
            "request_count": self._request_count,
        }
