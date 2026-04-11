"""
Temporal Knowledge Graph — SQLite-backed entity-relation triples with
temporal validity bounds.

Supports:
- add_triple(subject, predicate, object, valid_from) — insert a fact
- invalidate(subject, predicate, object) — expire a fact
- query_entity(entity, as_of) — get all facts about an entity at a point in time
- find_connected(entity, max_depth) — BFS traversal for connected topics

Forked from MemPalace knowledge_graph.py pattern (C2 in master plan).
Wire into: PromptEnricher as additional context signal, REMCycle for
fact extraction from conversation logs.

Usage:
    graph = TemporalKnowledgeGraph()
    graph.add_triple("client_acme", "rate", "$150/hr")
    graph.add_triple("client_acme", "industry", "fintech")
    facts = graph.query_entity("client_acme")
    connected = graph.find_connected("client_acme", max_depth=2)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_DEFAULT_DB_DIR = Path(__file__).parent / "db"


@dataclass
class Triple:
    """A single fact with temporal bounds."""
    subject: str
    predicate: str
    object: str
    valid_from: str  # ISO 8601
    valid_to: Optional[str] = None  # None = currently valid
    metadata: Dict[str, Any] = field(default_factory=dict)
    id: Optional[int] = None

    @property
    def is_current(self) -> bool:
        return self.valid_to is None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "metadata": self.metadata,
        }


class TemporalKnowledgeGraph:
    """
    SQLite-backed knowledge graph with temporal validity.

    Each triple (subject, predicate, object) has valid_from/valid_to timestamps.
    Invalidation sets valid_to without deleting — full history preserved.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = str(db_path or _DEFAULT_DB_DIR / "knowledge_graph.db")
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        conn = self._connect()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS triples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_to TEXT DEFAULT NULL,
                    metadata TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE INDEX IF NOT EXISTS idx_triples_subject
                    ON triples(subject);
                CREATE INDEX IF NOT EXISTS idx_triples_object
                    ON triples(object);
                CREATE INDEX IF NOT EXISTS idx_triples_predicate
                    ON triples(predicate);
                CREATE INDEX IF NOT EXISTS idx_triples_validity
                    ON triples(valid_from, valid_to);

                CREATE VIRTUAL TABLE IF NOT EXISTS triples_fts USING fts5(
                    subject, predicate, object,
                    content='triples',
                    content_rowid='id'
                );

                CREATE TRIGGER IF NOT EXISTS triples_ai AFTER INSERT ON triples BEGIN
                    INSERT INTO triples_fts(rowid, subject, predicate, object)
                    VALUES (new.id, new.subject, new.predicate, new.object);
                END;
            """)
            conn.commit()
        finally:
            conn.close()

    def add_triple(
        self,
        subject: str,
        predicate: str,
        object: str,
        valid_from: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Add a fact triple. Automatically invalidates any existing current
        triple with the same (subject, predicate) — facts are replaced, not
        stacked.

        Returns the new triple's row ID.
        """
        now = valid_from or datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)

        conn = self._connect()
        try:
            # Invalidate existing current triple with same (subject, predicate)
            # ONLY if the new fact is newer than the existing one — prevents
            # out-of-order backfills from corrupting the timeline.
            conn.execute(
                "UPDATE triples SET valid_to = ? "
                "WHERE subject = ? AND predicate = ? AND valid_to IS NULL "
                "AND valid_from <= ?",
                (now, subject, predicate, now),
            )

            cur = conn.execute(
                "INSERT INTO triples (subject, predicate, object, valid_from, metadata) "
                "VALUES (?, ?, ?, ?, ?)",
                (subject, predicate, object, now, meta_json),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def invalidate(
        self,
        subject: str,
        predicate: str,
        object: Optional[str] = None,
    ) -> int:
        """
        Expire fact(s). If object is None, invalidates ALL current facts
        matching (subject, predicate). Returns count of invalidated triples.
        """
        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            if object is not None:
                cur = conn.execute(
                    "UPDATE triples SET valid_to = ? "
                    "WHERE subject = ? AND predicate = ? AND object = ? AND valid_to IS NULL",
                    (now, subject, predicate, object),
                )
            else:
                cur = conn.execute(
                    "UPDATE triples SET valid_to = ? "
                    "WHERE subject = ? AND predicate = ? AND valid_to IS NULL",
                    (now, subject, predicate),
                )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def query_entity(
        self,
        entity: str,
        as_of: Optional[str] = None,
        include_expired: bool = False,
    ) -> List[Triple]:
        """
        Get all facts about an entity (as subject or object).

        Args:
            entity: Entity name to query.
            as_of: ISO timestamp — return facts valid at this point in time.
                   None = current facts only.
            include_expired: If True, also return expired facts.
        """
        conn = self._connect()
        try:
            if include_expired:
                rows = conn.execute(
                    "SELECT * FROM triples WHERE subject = ? OR object = ? "
                    "ORDER BY valid_from DESC",
                    (entity, entity),
                ).fetchall()
            elif as_of:
                rows = conn.execute(
                    "SELECT * FROM triples "
                    "WHERE (subject = ? OR object = ?) "
                    "AND valid_from <= ? "
                    "AND (valid_to IS NULL OR valid_to > ?) "
                    "ORDER BY valid_from DESC",
                    (entity, entity, as_of, as_of),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM triples "
                    "WHERE (subject = ? OR object = ?) AND valid_to IS NULL "
                    "ORDER BY valid_from DESC",
                    (entity, entity),
                ).fetchall()

            return [self._row_to_triple(r) for r in rows]
        finally:
            conn.close()

    def query_predicate(
        self,
        predicate: str,
        current_only: bool = True,
    ) -> List[Triple]:
        """Get all facts with a given predicate (e.g., all 'rate' facts)."""
        conn = self._connect()
        try:
            if current_only:
                rows = conn.execute(
                    "SELECT * FROM triples WHERE predicate = ? AND valid_to IS NULL "
                    "ORDER BY valid_from DESC",
                    (predicate,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM triples WHERE predicate = ? ORDER BY valid_from DESC",
                    (predicate,),
                ).fetchall()
            return [self._row_to_triple(r) for r in rows]
        finally:
            conn.close()

    def find_connected(
        self,
        entity: str,
        max_depth: int = 2,
        current_only: bool = True,
    ) -> Dict[str, List[Triple]]:
        """
        BFS traversal for connected entities.

        Returns {entity_name: [triples]} for all entities reachable within
        max_depth hops. Useful for cross-domain topic discovery.
        """
        visited: Set[str] = set()
        result: Dict[str, List[Triple]] = {}
        queue: deque[Tuple[str, int]] = deque([(entity, 0)])

        while queue:
            current, depth = queue.popleft()
            if current in visited or depth > max_depth:
                continue
            visited.add(current)

            triples = self.query_entity(current, include_expired=not current_only)
            if triples:
                result[current] = triples

            if depth < max_depth:
                for t in triples:
                    # Follow edges in both directions
                    neighbor = t.object if t.subject == current else t.subject
                    if neighbor not in visited:
                        queue.append((neighbor, depth + 1))

        return result

    def search(self, query: str, limit: int = 20) -> List[Triple]:
        """Full-text search across all triple fields."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT t.* FROM triples t "
                "JOIN triples_fts f ON t.id = f.rowid "
                "WHERE triples_fts MATCH ? "
                "ORDER BY rank LIMIT ?",
                (query, limit),
            ).fetchall()
            return [self._row_to_triple(r) for r in rows]
        finally:
            conn.close()

    def stats(self) -> Dict[str, Any]:
        """Return graph statistics."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT "
                "  COUNT(*) as total, "
                "  SUM(CASE WHEN valid_to IS NULL THEN 1 ELSE 0 END) as current, "
                "  COUNT(DISTINCT subject) as subjects, "
                "  COUNT(DISTINCT predicate) as predicates, "
                "  COUNT(DISTINCT object) as objects "
                "FROM triples"
            ).fetchone()
            return {
                "total_triples": row["total"] or 0,
                "current_triples": row["current"] or 0,
                "unique_subjects": row["subjects"] or 0,
                "unique_predicates": row["predicates"] or 0,
                "unique_objects": row["objects"] or 0,
            }
        finally:
            conn.close()

    def get_history(
        self,
        subject: str,
        predicate: str,
    ) -> List[Triple]:
        """Get full history of a (subject, predicate) pair, newest first."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM triples WHERE subject = ? AND predicate = ? "
                "ORDER BY valid_from DESC",
                (subject, predicate),
            ).fetchall()
            return [self._row_to_triple(r) for r in rows]
        finally:
            conn.close()

    def prune_stale(self, days: int = 90) -> int:
        """
        Archive triples not accessed in N days.
        Moves expired triples older than cutoff to an archive table.
        Returns count of archived triples.
        """
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        conn = self._connect()
        try:
            # Create archive table if needed
            conn.execute("""
                CREATE TABLE IF NOT EXISTS triples_archive (
                    id INTEGER PRIMARY KEY,
                    subject TEXT, predicate TEXT, object TEXT,
                    valid_from TEXT, valid_to TEXT, metadata TEXT,
                    created_at TEXT, archived_at TEXT DEFAULT (datetime('now'))
                )
            """)
            # Move expired triples older than cutoff
            cur = conn.execute(
                "INSERT INTO triples_archive "
                "  (id, subject, predicate, object, valid_from, valid_to, metadata, created_at) "
                "SELECT id, subject, predicate, object, valid_from, valid_to, metadata, created_at "
                "FROM triples WHERE valid_to IS NOT NULL AND valid_to < ?",
                (cutoff,),
            )
            archived = cur.rowcount
            conn.execute(
                "DELETE FROM triples WHERE valid_to IS NOT NULL AND valid_to < ?",
                (cutoff,),
            )
            conn.commit()
            return archived
        finally:
            conn.close()

    @staticmethod
    def _row_to_triple(row: sqlite3.Row) -> Triple:
        meta = {}
        try:
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
        except (json.JSONDecodeError, TypeError):
            pass
        return Triple(
            id=row["id"],
            subject=row["subject"],
            predicate=row["predicate"],
            object=row["object"],
            valid_from=row["valid_from"],
            valid_to=row["valid_to"],
            metadata=meta,
        )
