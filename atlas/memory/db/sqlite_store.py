"""
SQLite Memory Store
Structured storage for memory entries with full-text search.
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple, Any
from contextlib import contextmanager

# Import from parent module to avoid circular imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class SQLiteStore:
    """SQLite-based storage with FTS5 full-text search"""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        import zstandard as zstd
        self.compressor = zstd.ZstdCompressor(level=3)
        self.decompressor = zstd.ZstdDecompressor()
        self._init_db()

    def _compress(self, text: str) -> bytes:
        return self.compressor.compress(text.encode('utf-8'))

    def _decompress(self, data: Any) -> str:
        if isinstance(data, str):
            return data  # Legacy uncompressed text
        try:
            return self.decompressor.decompress(data).decode('utf-8')
        except Exception:
            # Fallback if somehow it's bytes but not zstd
            if isinstance(data, bytes):
                return data.decode('utf-8', errors='replace')
            return str(data)

    def _init_db(self):
        """Initialize database schema"""
        with self._connection() as conn:
            # Main memories table (content is BLOB for zstd)
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

            # Full-text search virtual table (stores uncompressed for searching)
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

            # Triggers: We DO NOT auto-sync `content` in the trigger anymore because the raw table has BLOBs 
            # and FTS5 needs TEXT. We will handle FTS insertion manually in Python.
            
            # Indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(memory_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_client ON memories(client_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_timestamp ON memories(timestamp)")

            conn.commit()

    @contextmanager
    def _connection(self):
        """Context manager for database connections"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def insert(self, entry) -> bool:
        """Insert a memory entry (compressed) and update FTS (uncompressed)"""
        compressed_content = self._compress(entry.content)
        
        with self._connection() as conn:
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
            
            # Manually update FTS table with uncompressed text
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
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM memories WHERE id = ?",
                (entry_id,)
            ).fetchone()

            if row:
                return self._row_to_entry(row)
        return None

    def search(
        self,
        query: str,
        memory_types: List = None,
        client_id: Optional[str] = None,
        limit: int = 10
    ) -> List[Tuple[Any, float]]:
        """Full-text search using FTS5 (which holds uncompressed text)"""
        results = []

        with self._connection() as conn:
            # Build FTS query
            fts_query = f'"{query}"*'  # Prefix matching

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
                # Normalize BM25 score (negative, lower is better) to 0-1 range
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
        with self._connection() as conn:
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
        with self._connection() as conn:
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
        with self._connection() as conn:
            conn.execute("DELETE FROM memories WHERE id = ?", (entry_id,))
            conn.commit()
            return conn.total_changes > 0

    def clear(self, memory_type=None, client_id: Optional[str] = None):
        """Clear memory entries (use with caution)"""
        with self._connection() as conn:
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
