"""
SQLite Memory Store
Structured storage for memory entries with full-text search.
"""

import sqlite3
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple, Any
from contextlib import contextmanager

# Import from parent module to avoid circular imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

logger = logging.getLogger(__name__)


class SQLiteStore:
    """SQLite-based storage with FTS5 full-text search and auto-recovery"""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        import zstandard as zstd
        self.compressor = zstd.ZstdCompressor(level=3)
        self.decompressor = zstd.ZstdDecompressor()
        self._recovered = False  # Track if we already recovered this session
        self._safe_init_db()

    def _compress(self, text: str) -> bytes:
        return self.compressor.compress(text.encode('utf-8'))

    def _decompress(self, data: Any) -> str:
        if isinstance(data, str):
            return data  # Legacy uncompressed text
        try:
            return self.decompressor.decompress(data).decode('utf-8')
        except Exception:
            if isinstance(data, bytes):
                return data.decode('utf-8', errors='replace')
            return str(data)

    def _nuke_and_rebuild(self, error: Exception):
        """Nuclear option: backup corrupted DB and recreate from scratch."""
        if self._recovered:
            # Already recovered once this session — don't loop
            logger.error(f"DB corruption after recovery — giving up: {error}")
            return
        self._recovered = True
        logger.warning(f"Corrupted memory DB detected during operation, rebuilding: {error}")
        # Backup corrupted file
        backup = self.db_path.with_suffix(f".db.corrupted.{datetime.utcnow().strftime('%Y%m%d%H%M%S')}")
        try:
            if self.db_path.exists():
                self.db_path.rename(backup)
            # Remove WAL/SHM
            for suffix in ("-wal", "-shm"):
                p = Path(str(self.db_path) + suffix)
                if p.exists():
                    p.unlink()
        except OSError as e:
            logger.error(f"Failed to backup corrupted DB: {e}")
        # Recreate
        self._init_db()
        logger.info(f"Memory DB rebuilt successfully (backup: {backup})")

    def _safe_init_db(self):
        """Initialize DB with corruption recovery."""
        try:
            self._init_db()
            # Verify the DB is actually readable (not just table creation)
            with self._raw_connection() as conn:
                conn.execute("SELECT COUNT(*) FROM memories")
        except sqlite3.DatabaseError as e:
            if "malformed" in str(e) or "corrupt" in str(e) or "disk image" in str(e):
                self._nuke_and_rebuild(e)
            else:
                raise

    def _init_db(self):
        """Initialize database schema"""
        with self._raw_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    content BLOB NOT NULL,
                    memory_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    metadata TEXT,
                    client_id TEXT,
                    session_id TEXT,
                    relevance_score REAL DEFAULT 1.0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                    id,
                    content,
                    memory_type,
                    client_id,
                    content='memories',
                    content_rowid='rowid'
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(memory_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_client ON memories(client_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_timestamp ON memories(timestamp)")
            conn.commit()

    @contextmanager
    def _raw_connection(self):
        """Raw connection without corruption recovery (used during init/rebuild)."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            yield conn
        finally:
            conn.close()

    # All public methods (insert, search, get, get_recent) handle corruption
    # individually via try/except + _nuke_and_rebuild, so _raw_connection is
    # the only connection method needed.

    def insert(self, entry) -> bool:
        """Insert a memory entry (compressed) and update FTS (uncompressed)"""
        try:
            return self._insert_impl(entry)
        except sqlite3.DatabaseError as e:
            if "malformed" in str(e) or "corrupt" in str(e) or "disk image" in str(e):
                self._nuke_and_rebuild(e)
                return self._insert_impl(entry)  # Retry on fresh DB
            raise

    def _insert_impl(self, entry) -> bool:
        compressed_content = self._compress(entry.content)
        with self._raw_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO memories
                (id, content, memory_type, timestamp, metadata, client_id, session_id, relevance_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entry.id,
                sqlite3.Binary(compressed_content),
                entry.memory_type.value,
                entry.timestamp.isoformat(),
                json.dumps(entry.metadata),
                entry.client_id,
                entry.session_id,
                entry.relevance_score
            ))
            rowid = cursor.lastrowid
            cursor.execute("DELETE FROM memories_fts WHERE id = ?", (entry.id,))
            cursor.execute("""
                INSERT INTO memories_fts(rowid, id, content, memory_type, client_id)
                VALUES (?, ?, ?, ?, ?)
            """, (
                rowid,
                entry.id,
                entry.content,
                entry.memory_type.value,
                entry.client_id
            ))
            conn.commit()
        return True

    def get(self, entry_id: str):
        """Get a specific memory entry"""
        try:
            with self._raw_connection() as conn:
                row = conn.execute(
                    "SELECT * FROM memories WHERE id = ?",
                    (entry_id,)
                ).fetchone()
                if row:
                    return self._row_to_entry(row)
            return None
        except sqlite3.DatabaseError as e:
            if "malformed" in str(e) or "corrupt" in str(e) or "disk image" in str(e):
                self._nuke_and_rebuild(e)
                return None  # Data is gone after rebuild
            raise

    def search(
        self,
        query: str,
        memory_types: List = None,
        client_id: Optional[str] = None,
        limit: int = 10
    ) -> List[Tuple[Any, float]]:
        """Full-text search using FTS5 (which holds uncompressed text)"""
        try:
            return self._search_impl(query, memory_types, client_id, limit)
        except sqlite3.DatabaseError as e:
            if "malformed" in str(e) or "corrupt" in str(e) or "disk image" in str(e):
                self._nuke_and_rebuild(e)
                return []  # Empty results after rebuild
            raise

    def _search_impl(self, query, memory_types, client_id, limit):
        results = []
        with self._raw_connection() as conn:
            fts_query = f'"{query}"*'
            sql = """
                SELECT m.*, bm25(memories_fts) as score
                FROM memories_fts
                JOIN memories m ON memories_fts.id = m.id
                WHERE memories_fts MATCH ?
            """
            params = [fts_query]
            if memory_types:
                placeholders = ",".join("?" * len(memory_types))
                sql += f" AND m.memory_type IN ({placeholders})"
                params.extend([mt.value for mt in memory_types])
            if client_id:
                sql += " AND (m.client_id = ? OR m.client_id IS NULL)"
                params.append(client_id)
            sql += " ORDER BY score LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            for row in rows:
                entry = self._row_to_entry(row)
                score = 1.0 / (1.0 + abs(row['score']))
                results.append((entry, score))
        return results

    def get_recent(
        self,
        memory_type=None,
        client_id: Optional[str] = None,
        limit: int = 10
    ) -> List:
        """Get recent memory entries"""
        try:
            return self._get_recent_impl(memory_type, client_id, limit)
        except sqlite3.DatabaseError as e:
            if "malformed" in str(e) or "corrupt" in str(e) or "disk image" in str(e):
                self._nuke_and_rebuild(e)
                return []
            raise

    def _get_recent_impl(self, memory_type, client_id, limit):
        with self._raw_connection() as conn:
            sql = "SELECT * FROM memories WHERE 1=1"
            params = []
            if memory_type:
                sql += " AND memory_type = ?"
                params.append(memory_type.value)
            if client_id:
                sql += " AND (client_id = ? OR client_id IS NULL)"
                params.append(client_id)
            sql += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_entry(row) for row in rows]

    def _row_to_entry(self, row):
        """Convert database row to MemoryEntry, decompressing content"""
        from memory.hybrid_memory import MemoryEntry, MemoryType

        return MemoryEntry(
            id=row['id'],
            content=self._decompress(row['content']),
            memory_type=MemoryType(row['memory_type']),
            timestamp=datetime.fromisoformat(row['timestamp']),
            metadata=json.loads(row['metadata']) if row['metadata'] else {},
            client_id=row['client_id'],
            session_id=row['session_id'],
            relevance_score=row['relevance_score']
        )

    def count(self, memory_type=None, client_id: Optional[str] = None) -> int:
        """Count memory entries"""
        with self._raw_connection() as conn:
            sql = "SELECT COUNT(*) FROM memories WHERE 1=1"
            params = []

            if memory_type:
                sql += " AND memory_type = ?"
                params.append(memory_type.value)

            if client_id:
                sql += " AND client_id = ?"
                params.append(client_id)

            return conn.execute(sql, params).fetchone()[0]

    def delete(self, entry_id: str) -> bool:
        """Delete a memory entry"""
        with self._raw_connection() as conn:
            conn.execute("DELETE FROM memories WHERE id = ?", (entry_id,))
            conn.commit()
            return conn.total_changes > 0

    def clear(self, memory_type=None, client_id: Optional[str] = None):
        """Clear memory entries (use with caution)"""
        with self._raw_connection() as conn:
            sql = "DELETE FROM memories WHERE 1=1"
            params = []

            if memory_type:
                sql += " AND memory_type = ?"
                params.append(memory_type.value)

            if client_id:
                sql += " AND client_id = ?"
                params.append(client_id)

            conn.execute(sql, params)
            conn.commit()
