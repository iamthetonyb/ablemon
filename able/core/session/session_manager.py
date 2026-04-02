"""
Session Manager — Centralized conversation-level state tracking.

SQLite-backed with LRU cache and auto-expiry. Used by the CLI agent,
SDK, gateway, billing, and analytics pipelines.

DB path: data/sessions.db
"""

import json
import logging
import sqlite3
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = str(_PROJECT_ROOT / "data" / "sessions.db")


@dataclass
class Session:
    """Conversation-level state for a single session."""

    conversation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str = "default"
    messages: int = 0
    tools_used: List[str] = field(default_factory=list)
    complexity_scores: List[float] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    cost_usd: float = 0.0
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Computed helpers (not persisted directly — derived on read)
    @property
    def avg_complexity(self) -> float:
        if not self.complexity_scores:
            return 0.0
        return sum(self.complexity_scores) / len(self.complexity_scores)

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens


class SessionManager:
    """
    SQLite-backed session store with LRU in-memory cache.

    - get_or_create(): fetch existing session or create a new one
    - update(): append message stats to an existing session
    - expire_stale(): purge sessions older than TTL
    - get_stats(): aggregate stats across all active sessions
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS sessions (
        conversation_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL DEFAULT 'default',
        messages INTEGER NOT NULL DEFAULT 0,
        tools_used TEXT NOT NULL DEFAULT '[]',
        complexity_scores TEXT NOT NULL DEFAULT '[]',
        total_input_tokens INTEGER NOT NULL DEFAULT 0,
        total_output_tokens INTEGER NOT NULL DEFAULT 0,
        cost_usd REAL NOT NULL DEFAULT 0.0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        metadata TEXT NOT NULL DEFAULT '{}'
    );

    CREATE INDEX IF NOT EXISTS idx_sessions_tenant ON sessions(tenant_id);
    CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at);
    """

    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        ttl_seconds: int = 1800,
        cache_max: int = 100,
    ):
        self._db_path = db_path
        self._ttl_seconds = ttl_seconds
        self._cache_max = cache_max
        self._cache: OrderedDict[str, Session] = OrderedDict()

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── Database setup ────────────────────────────────────────────

    def _init_db(self) -> None:
        conn = sqlite3.connect(self._db_path)
        try:
            conn.executescript(self.SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.commit()
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Cache helpers ─────────────────────────────────────────────

    def _cache_put(self, session: Session) -> None:
        key = session.conversation_id
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = session
        while len(self._cache) > self._cache_max:
            self._cache.popitem(last=False)

    def _cache_get(self, conversation_id: str) -> Optional[Session]:
        if conversation_id in self._cache:
            self._cache.move_to_end(conversation_id)
            return self._cache[conversation_id]
        return None

    # ── Row <-> Session conversion ────────────────────────────────

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> Session:
        return Session(
            conversation_id=row["conversation_id"],
            tenant_id=row["tenant_id"],
            messages=row["messages"],
            tools_used=json.loads(row["tools_used"]),
            complexity_scores=json.loads(row["complexity_scores"]),
            total_input_tokens=row["total_input_tokens"],
            total_output_tokens=row["total_output_tokens"],
            cost_usd=row["cost_usd"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=json.loads(row["metadata"]),
        )

    def _session_to_params(self, session: Session) -> dict:
        return {
            "conversation_id": session.conversation_id,
            "tenant_id": session.tenant_id,
            "messages": session.messages,
            "tools_used": json.dumps(session.tools_used),
            "complexity_scores": json.dumps(session.complexity_scores),
            "total_input_tokens": session.total_input_tokens,
            "total_output_tokens": session.total_output_tokens,
            "cost_usd": session.cost_usd,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "metadata": json.dumps(session.metadata),
        }

    # ── Public API ────────────────────────────────────────────────

    def get_or_create(
        self,
        conversation_id: str,
        tenant_id: str = "default",
    ) -> Session:
        """Return an existing session or create a new one."""
        cached = self._cache_get(conversation_id)
        if cached is not None:
            return cached

        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM sessions WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()

            if row:
                session = self._row_to_session(row)
                self._cache_put(session)
                return session

            session = Session(
                conversation_id=conversation_id,
                tenant_id=tenant_id,
            )
            params = self._session_to_params(session)
            conn.execute(
                """INSERT INTO sessions
                   (conversation_id, tenant_id, messages, tools_used,
                    complexity_scores, total_input_tokens, total_output_tokens,
                    cost_usd, created_at, updated_at, metadata)
                   VALUES (:conversation_id, :tenant_id, :messages, :tools_used,
                           :complexity_scores, :total_input_tokens, :total_output_tokens,
                           :cost_usd, :created_at, :updated_at, :metadata)""",
                params,
            )
            conn.commit()
            self._cache_put(session)
            return session
        finally:
            conn.close()

    def update(
        self,
        conversation_id: str,
        *,
        tools_used: Optional[List[str]] = None,
        complexity_score: Optional[float] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        metadata_patch: Optional[Dict[str, Any]] = None,
    ) -> Optional[Session]:
        """
        Append stats from one message turn to a session.

        Returns the updated Session, or None if the session doesn't exist.
        """
        session = self.get_or_create(conversation_id)

        session.messages += 1
        if tools_used:
            session.tools_used.extend(tools_used)
        if complexity_score is not None:
            session.complexity_scores.append(complexity_score)
        session.total_input_tokens += input_tokens
        session.total_output_tokens += output_tokens
        session.cost_usd += cost_usd
        if metadata_patch:
            session.metadata.update(metadata_patch)
        session.updated_at = datetime.now(timezone.utc).isoformat()

        params = self._session_to_params(session)
        conn = self._conn()
        try:
            conn.execute(
                """UPDATE sessions SET
                       messages = :messages,
                       tools_used = :tools_used,
                       complexity_scores = :complexity_scores,
                       total_input_tokens = :total_input_tokens,
                       total_output_tokens = :total_output_tokens,
                       cost_usd = :cost_usd,
                       updated_at = :updated_at,
                       metadata = :metadata
                   WHERE conversation_id = :conversation_id""",
                params,
            )
            conn.commit()
        finally:
            conn.close()

        self._cache_put(session)
        return session

    def expire_stale(self) -> int:
        """Remove sessions not updated within TTL. Returns count expired."""
        cutoff = datetime.now(timezone.utc).timestamp() - self._ttl_seconds
        # Convert to ISO for comparison (SQLite text comparison works for ISO timestamps)
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()

        conn = self._conn()
        try:
            cursor = conn.execute(
                "DELETE FROM sessions WHERE updated_at < ?", (cutoff_iso,)
            )
            conn.commit()
            expired = cursor.rowcount

            # Evict from cache too
            stale_keys = [
                k for k, s in self._cache.items() if s.updated_at < cutoff_iso
            ]
            for k in stale_keys:
                del self._cache[k]

            if expired:
                logger.info(f"Expired {expired} stale session(s)")
            return expired
        finally:
            conn.close()

    def get_stats(self) -> Dict[str, Any]:
        """Aggregate stats across all active (non-expired) sessions."""
        conn = self._conn()
        try:
            row = conn.execute(
                """SELECT
                       COUNT(*) as total_sessions,
                       COALESCE(SUM(messages), 0) as total_messages,
                       COALESCE(SUM(total_input_tokens), 0) as total_input_tokens,
                       COALESCE(SUM(total_output_tokens), 0) as total_output_tokens,
                       COALESCE(SUM(cost_usd), 0.0) as total_cost_usd
                   FROM sessions"""
            ).fetchone()

            return {
                "total_sessions": row["total_sessions"],
                "total_messages": row["total_messages"],
                "total_input_tokens": row["total_input_tokens"],
                "total_output_tokens": row["total_output_tokens"],
                "total_cost_usd": round(row["total_cost_usd"], 6),
            }
        finally:
            conn.close()

    def get(self, conversation_id: str) -> Optional[Session]:
        """Retrieve a session by ID, or None."""
        cached = self._cache_get(conversation_id)
        if cached is not None:
            return cached

        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM sessions WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if row:
                session = self._row_to_session(row)
                self._cache_put(session)
                return session
            return None
        finally:
            conn.close()
