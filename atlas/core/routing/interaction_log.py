"""
Interaction Logger — Structured logging for multi-tier routing decisions.

Stores every routed interaction in SQLite for the M2.7 evolution daemon
to analyze and improve routing accuracy over time.

Schema: interaction_log table with InteractionRecord fields.
Query helpers: atlas/core/routing/log_queries.py
"""

import logging
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "data/interaction_log.db"


@dataclass
class InteractionRecord:
    """Single routed interaction record."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # ── Routing decision ──────────────────────────────────────
    message_preview: str = ""  # First 200 chars (no PII in full msg)
    complexity_score: float = 0.0
    selected_tier: int = 1
    selected_provider: str = ""
    domain: str = "default"
    features: str = ""  # JSON-serialized features dict
    scorer_version: int = 1
    budget_gated: bool = False

    # ── Execution result ──────────────────────────────────────
    actual_provider: str = ""  # May differ if fallback triggered
    fallback_used: bool = False
    fallback_chain: str = ""  # Comma-separated providers tried
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    # ── Quality signals ───────────────────────────────────────
    success: bool = True
    error_type: str = ""  # timeout, rate_limit, auth, content_filter, etc.
    user_correction: bool = False  # User manually escalated/overrode
    user_satisfaction: Optional[int] = None  # 1-5 if collected
    escalated: bool = False  # Tier was too low, had to escalate

    # ── Context ───────────────────────────────────────────────
    channel: str = ""  # telegram, discord, cli, api
    session_id: str = ""
    conversation_turn: int = 0


class InteractionLogger:
    """
    SQLite-backed interaction logger.

    Thread-safe via SQLite WAL mode. Designed for append-heavy workload
    with periodic reads by the evolution daemon.
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS interaction_log (
        id TEXT PRIMARY KEY,
        timestamp TEXT NOT NULL,
        message_preview TEXT,
        complexity_score REAL,
        selected_tier INTEGER,
        selected_provider TEXT,
        domain TEXT,
        features TEXT,
        scorer_version INTEGER,
        budget_gated INTEGER DEFAULT 0,
        actual_provider TEXT,
        fallback_used INTEGER DEFAULT 0,
        fallback_chain TEXT,
        latency_ms REAL,
        input_tokens INTEGER DEFAULT 0,
        output_tokens INTEGER DEFAULT 0,
        cost_usd REAL DEFAULT 0.0,
        success INTEGER DEFAULT 1,
        error_type TEXT,
        user_correction INTEGER DEFAULT 0,
        user_satisfaction INTEGER,
        escalated INTEGER DEFAULT 0,
        channel TEXT,
        session_id TEXT,
        conversation_turn INTEGER DEFAULT 0
    );

    CREATE INDEX IF NOT EXISTS idx_log_timestamp ON interaction_log(timestamp);
    CREATE INDEX IF NOT EXISTS idx_log_tier ON interaction_log(selected_tier);
    CREATE INDEX IF NOT EXISTS idx_log_provider ON interaction_log(selected_provider);
    CREATE INDEX IF NOT EXISTS idx_log_domain ON interaction_log(domain);
    CREATE INDEX IF NOT EXISTS idx_log_success ON interaction_log(success);
    CREATE INDEX IF NOT EXISTS idx_log_scorer_version ON interaction_log(scorer_version);
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Create tables and indices if they don't exist."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.executescript(self.SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        """Get a connection with row factory."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def log(self, record: InteractionRecord) -> str:
        """
        Insert an interaction record. Returns the record ID.
        """
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO interaction_log (
                    id, timestamp, message_preview, complexity_score,
                    selected_tier, selected_provider, domain, features,
                    scorer_version, budget_gated, actual_provider,
                    fallback_used, fallback_chain, latency_ms,
                    input_tokens, output_tokens, cost_usd,
                    success, error_type, user_correction,
                    user_satisfaction, escalated, channel,
                    session_id, conversation_turn
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?
                )""",
                (
                    record.id,
                    record.timestamp,
                    record.message_preview[:200],
                    record.complexity_score,
                    record.selected_tier,
                    record.selected_provider,
                    record.domain,
                    record.features,
                    record.scorer_version,
                    int(record.budget_gated),
                    record.actual_provider,
                    int(record.fallback_used),
                    record.fallback_chain,
                    record.latency_ms,
                    record.input_tokens,
                    record.output_tokens,
                    record.cost_usd,
                    int(record.success),
                    record.error_type,
                    int(record.user_correction),
                    record.user_satisfaction,
                    int(record.escalated),
                    record.channel,
                    record.session_id,
                    record.conversation_turn,
                ),
            )
            conn.commit()
            return record.id
        finally:
            conn.close()

    def update_result(
        self,
        record_id: str,
        *,
        actual_provider: Optional[str] = None,
        fallback_used: Optional[bool] = None,
        fallback_chain: Optional[str] = None,
        latency_ms: Optional[float] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        cost_usd: Optional[float] = None,
        success: Optional[bool] = None,
        error_type: Optional[str] = None,
    ):
        """
        Update execution results after a provider responds.

        Allows logging the routing decision at dispatch time and
        filling in results asynchronously.
        """
        updates = []
        values = []
        for col, val in [
            ("actual_provider", actual_provider),
            ("fallback_used", int(fallback_used) if fallback_used is not None else None),
            ("fallback_chain", fallback_chain),
            ("latency_ms", latency_ms),
            ("input_tokens", input_tokens),
            ("output_tokens", output_tokens),
            ("cost_usd", cost_usd),
            ("success", int(success) if success is not None else None),
            ("error_type", error_type),
        ]:
            if val is not None:
                updates.append(f"{col} = ?")
                values.append(val)

        if not updates:
            return

        values.append(record_id)
        conn = self._connect()
        try:
            conn.execute(
                f"UPDATE interaction_log SET {', '.join(updates)} WHERE id = ?",
                values,
            )
            conn.commit()
        finally:
            conn.close()

    def mark_user_correction(self, record_id: str):
        """Mark that the user manually escalated or overrode routing."""
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE interaction_log SET user_correction = 1 WHERE id = ?",
                (record_id,),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_escalated(self, record_id: str):
        """Mark that the tier was too low and had to be escalated."""
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE interaction_log SET escalated = 1 WHERE id = ?",
                (record_id,),
            )
            conn.commit()
        finally:
            conn.close()

    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a single record by ID."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM interaction_log WHERE id = ?", (record_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get most recent interactions."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM interaction_log ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def count(self) -> int:
        """Total interaction count."""
        conn = self._connect()
        try:
            return conn.execute("SELECT COUNT(*) FROM interaction_log").fetchone()[0]
        finally:
            conn.close()

    @property
    def db_path(self) -> str:
        return self._db_path
