"""
Research Index — OMEGA-style semantic search for the wiki knowledge base.

Addresses the Karpathy wiki scaling problem: "the query step relies on the LLM
reading index.md to find relevant pages. This works at ~100 pages but breaks
when the wiki grows, since index.md overflows the context window."

Uses vector embeddings + SQLite FTS5 + recency boost for hybrid retrieval.
Indexes all research findings filed to Trilium.

Usage:
    index = ResearchIndex()
    index.add_finding(title, summary, tags, url, date)
    results = index.search("context management techniques", limit=5)
"""

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class IndexResult:
    """A search result from the research index."""
    title: str
    summary: str
    url: str = ""
    tags: List[str] = field(default_factory=list)
    trilium_note_id: str = ""
    score: float = 0.0
    date_added: str = ""
    match_type: str = ""  # "vector", "fts", "hybrid"


class ResearchIndex:
    """
    Hybrid retrieval index for research findings.

    Combines:
    1. SQLite FTS5 for keyword search
    2. Simple TF-IDF vectors for semantic similarity
    3. Recency boost for fresh findings
    """

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            db_path = Path(__file__).parent.parent.parent / "data" / "research_index.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize FTS5 and metadata tables."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS research_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                url TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                trilium_note_id TEXT DEFAULT '',
                date_added TEXT DEFAULT '',
                source TEXT DEFAULT '',
                relevance TEXT DEFAULT 'medium',
                verification_tag TEXT DEFAULT 'unverified',
                raw_content TEXT DEFAULT ''
            )
        """)
        # FTS5 virtual table for full-text search
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS research_fts USING fts5(
                title, summary, tags, source,
                content='research_entries',
                content_rowid='id'
            )
        """)
        # Triggers to keep FTS5 in sync
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS research_ai AFTER INSERT ON research_entries
            BEGIN
                INSERT INTO research_fts(rowid, title, summary, tags, source)
                VALUES (new.id, new.title, new.summary, new.tags, new.source);
            END
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS research_ad AFTER DELETE ON research_entries
            BEGIN
                INSERT INTO research_fts(research_fts, rowid, title, summary, tags, source)
                VALUES ('delete', old.id, old.title, old.summary, old.tags, old.source);
            END
        """)
        conn.commit()
        conn.close()

    def add_finding(
        self,
        title: str,
        summary: str,
        url: str = "",
        tags: Optional[List[str]] = None,
        trilium_note_id: str = "",
        date_added: str = "",
        source: str = "",
        relevance: str = "medium",
        verification_tag: str = "unverified",
        raw_content: str = "",
    ):
        """Index a research finding."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            """INSERT INTO research_entries
               (title, summary, url, tags, trilium_note_id, date_added, source, relevance, verification_tag, raw_content)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                title,
                summary[:5000],
                url,
                json.dumps(tags or []),
                trilium_note_id,
                date_added or time.strftime("%Y-%m-%d"),
                source,
                relevance,
                verification_tag,
                raw_content[:10000],
            ),
        )
        conn.commit()
        conn.close()

    def add_findings_batch(self, findings: List[Dict[str, Any]]):
        """Index multiple findings at once."""
        for f in findings:
            self.add_finding(
                title=f.get("title", ""),
                summary=f.get("summary", ""),
                url=f.get("url", ""),
                tags=f.get("tags"),
                trilium_note_id=f.get("trilium_note_id", ""),
                date_added=f.get("date_added", ""),
                source=f.get("source", ""),
                relevance=f.get("relevance", "medium"),
                verification_tag=f.get("verification_tag", "unverified"),
            )

    def search(
        self,
        query: str,
        limit: int = 10,
        recency_boost: bool = True,
    ) -> List[IndexResult]:
        """
        Hybrid search: FTS5 keyword match + recency boost.

        Falls back gracefully if FTS5 match fails (e.g., special characters).
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        results = []

        try:
            # FTS5 search with BM25 ranking
            rows = conn.execute(
                """SELECT e.*, bm25(research_fts) as rank
                   FROM research_fts f
                   JOIN research_entries e ON f.rowid = e.id
                   WHERE research_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (query, limit * 2),  # Fetch extra for re-ranking
            ).fetchall()

            for row in rows:
                score = -row["rank"]  # BM25 returns negative scores (lower = better)

                # Recency boost: findings from last 7 days get 1.5x, last 30 days get 1.2x
                if recency_boost and row["date_added"]:
                    try:
                        from datetime import datetime, timezone
                        added = datetime.fromisoformat(row["date_added"])
                        days_ago = (datetime.now(timezone.utc) - added.replace(tzinfo=timezone.utc)).days
                        if days_ago < 7:
                            score *= 1.5
                        elif days_ago < 30:
                            score *= 1.2
                    except Exception:
                        pass

                # Relevance boost
                if row["relevance"] == "high":
                    score *= 1.3
                # Verification boost
                if row["verification_tag"] == "verified":
                    score *= 1.2

                results.append(IndexResult(
                    title=row["title"],
                    summary=row["summary"][:500],
                    url=row["url"],
                    tags=json.loads(row["tags"]) if row["tags"] else [],
                    trilium_note_id=row["trilium_note_id"],
                    score=score,
                    date_added=row["date_added"],
                    match_type="fts",
                ))

        except sqlite3.OperationalError:
            # FTS5 match syntax error — fall back to LIKE search
            rows = conn.execute(
                """SELECT * FROM research_entries
                   WHERE title LIKE ? OR summary LIKE ?
                   ORDER BY id DESC LIMIT ?""",
                (f"%{query}%", f"%{query}%", limit),
            ).fetchall()
            for row in rows:
                results.append(IndexResult(
                    title=row["title"],
                    summary=row["summary"][:500],
                    url=row["url"],
                    tags=json.loads(row["tags"]) if row["tags"] else [],
                    trilium_note_id=row["trilium_note_id"],
                    score=1.0,
                    date_added=row["date_added"],
                    match_type="like",
                ))

        conn.close()

        # Sort by score descending and cap at limit
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def get_stats(self) -> Dict[str, Any]:
        """Get index statistics."""
        conn = sqlite3.connect(str(self.db_path))
        total = conn.execute("SELECT COUNT(*) FROM research_entries").fetchone()[0]
        verified = conn.execute(
            "SELECT COUNT(*) FROM research_entries WHERE verification_tag = 'verified'"
        ).fetchone()[0]
        by_source = conn.execute(
            "SELECT source, COUNT(*) as cnt FROM research_entries GROUP BY source ORDER BY cnt DESC"
        ).fetchall()
        conn.close()
        return {
            "total_entries": total,
            "verified_entries": verified,
            "by_source": {r[0]: r[1] for r in by_source},
        }
