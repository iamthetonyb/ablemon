"""
CVC Context Versioning — Merkle DAG snapshots for conversation state.

Saves context snapshots at decision boundaries (before tool calls,
before tier escalations). Enables rollback if an expensive model call
fails or returns low confidence.

Storage: SQLite `context_snapshots` table in sessions.db.

Usage:
    store = ContextVersionStore()

    # Save before expensive operation
    hash = store.save_snapshot(session_id, messages, {"reason": "pre-T4-escalation"})

    # If operation fails, rollback
    messages = store.rollback(session_id, hash)

    # List snapshots for a session
    snapshots = store.list_snapshots(session_id)
"""

import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ContextVersionStore:
    """
    SQLite-backed context snapshot store.

    Each snapshot stores:
    - Session ID
    - SHA-256 hash of the serialized messages
    - The serialized messages themselves
    - Metadata (reason, tier, confidence, etc.)
    - Timestamp
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS context_snapshots (
        snapshot_hash TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        messages_json TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        message_count INTEGER NOT NULL DEFAULT 0,
        estimated_tokens INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_snapshots_session
        ON context_snapshots(session_id, created_at DESC);
    """

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or str(
            Path(__file__).parent.parent.parent.parent / "data" / "sessions.db"
        )
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self._db_path)
        try:
            conn.executescript(self.SCHEMA)
        finally:
            conn.close()

    def save_snapshot(
        self,
        session_id: str,
        messages: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Save a context snapshot. Returns the snapshot hash.

        Call this before:
        - Tier escalations (T1→T2, T2→T4)
        - Tool calls that modify state
        - Sending to expensive models
        """
        from able.core.session.context_compactor import ContextCompactor

        messages_json = json.dumps(messages, default=str)
        snapshot_hash = hashlib.sha256(messages_json.encode()).hexdigest()[:16]

        compactor = ContextCompactor()
        estimated_tokens = compactor.estimate_tokens(messages)

        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                """INSERT OR REPLACE INTO context_snapshots
                   (snapshot_hash, session_id, messages_json, metadata_json,
                    message_count, estimated_tokens, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot_hash,
                    session_id,
                    messages_json,
                    json.dumps(metadata or {}, default=str),
                    len(messages),
                    estimated_tokens,
                    time.time(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info(
            "Context snapshot saved: %s (session=%s, messages=%d, ~%d tokens)",
            snapshot_hash,
            session_id,
            len(messages),
            estimated_tokens,
        )
        return snapshot_hash

    def rollback(self, session_id: str, snapshot_hash: str) -> Optional[List[Dict[str, Any]]]:
        """
        Restore messages from a previous snapshot.
        Returns the message list, or None if snapshot not found.
        """
        conn = sqlite3.connect(self._db_path)
        try:
            row = conn.execute(
                "SELECT messages_json FROM context_snapshots "
                "WHERE snapshot_hash = ? AND session_id = ?",
                (snapshot_hash, session_id),
            ).fetchone()
        finally:
            conn.close()

        if not row:
            logger.warning("Snapshot %s not found for session %s", snapshot_hash, session_id)
            return None

        messages = json.loads(row[0])
        logger.info(
            "Context rolled back to snapshot %s (%d messages)",
            snapshot_hash,
            len(messages),
        )
        return messages

    def get_latest_snapshot(self, session_id: str) -> Optional[str]:
        """Get the most recent snapshot hash for a session."""
        conn = sqlite3.connect(self._db_path)
        try:
            row = conn.execute(
                "SELECT snapshot_hash FROM context_snapshots "
                "WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        finally:
            conn.close()

        return row[0] if row else None

    def list_snapshots(self, session_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """List all snapshots for a session."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT snapshot_hash, message_count, estimated_tokens, "
                "metadata_json, created_at FROM context_snapshots "
                "WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        finally:
            conn.close()

        return [
            {
                "hash": r["snapshot_hash"],
                "messages": r["message_count"],
                "tokens": r["estimated_tokens"],
                "metadata": json.loads(r["metadata_json"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def prune(self, session_id: str, keep: int = 10):
        """Keep only the N most recent snapshots for a session."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "DELETE FROM context_snapshots WHERE session_id = ? "
                "AND snapshot_hash NOT IN ("
                "  SELECT snapshot_hash FROM context_snapshots "
                "  WHERE session_id = ? ORDER BY created_at DESC LIMIT ?"
                ")",
                (session_id, session_id, keep),
            )
            conn.commit()
        finally:
            conn.close()
