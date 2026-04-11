"""
Research Index — OMEGA-style semantic search for the wiki knowledge base.

Addresses the Karpathy wiki scaling problem: "the query step relies on the LLM
reading index.md to find relevant pages. This works at ~100 pages but breaks
when the wiki grows, since index.md overflows the context window."

Uses three-tier retrieval (C3 Smart Search Pipeline):
  1. BM25 via SQLite FTS5 (keyword match)
  2. Vector similarity (semantic match via embeddings)
  3. Optional LLM reranking (precision boost)

Combined via Reciprocal Rank Fusion (RRF, k=60).

Also includes smart chunking with break-point scoring (QMD fork):
  h1=100, h2=90, code_fence=80, paragraph=20, squared distance decay.
  Never splits inside code fences.

Usage:
    index = ResearchIndex()
    index.add_finding(title, summary, tags, url, date)
    results = index.search("context management techniques", limit=5)
    # Three-tier with reranking:
    results = index.smart_search("context management", limit=5, rerank_fn=my_llm)
"""

import json
import logging
import math
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Smart Chunking (QMD fork) ─────────────────────────────────

# Break-point scores — higher = stronger boundary signal
_BREAK_SCORES = {
    "h1": 100,
    "h2": 90,
    "h3": 80,
    "code_fence": 80,
    "hr": 70,
    "paragraph": 20,
    "newline": 5,
}

_H1_RE = re.compile(r"^# .+", re.MULTILINE)
_H2_RE = re.compile(r"^## .+", re.MULTILINE)
_H3_RE = re.compile(r"^### .+", re.MULTILINE)
_FENCE_RE = re.compile(r"^```", re.MULTILINE)
_HR_RE = re.compile(r"^---+\s*$", re.MULTILINE)
_PARA_RE = re.compile(r"\n\n+")


def smart_chunk(
    text: str,
    max_chunk_size: int = 800,
    overlap: int = 50,
) -> List[str]:
    """
    Break text into chunks at natural boundaries.

    Uses break-point scoring with squared distance decay:
    - Headers, code fences, horizontal rules are strong boundaries
    - Paragraphs are moderate boundaries
    - Never splits inside a code fence

    Returns list of chunk strings.
    """
    if len(text) <= max_chunk_size:
        return [text]

    # Clamp overlap to avoid backward progress
    overlap = min(overlap, max_chunk_size // 4)

    # Find all break points with scores
    breaks: List[Tuple[int, int]] = []  # (position, score)

    for m in _H1_RE.finditer(text):
        breaks.append((m.start(), _BREAK_SCORES["h1"]))
    for m in _H2_RE.finditer(text):
        breaks.append((m.start(), _BREAK_SCORES["h2"]))
    for m in _H3_RE.finditer(text):
        breaks.append((m.start(), _BREAK_SCORES["h3"]))
    for m in _HR_RE.finditer(text):
        breaks.append((m.start(), _BREAK_SCORES["hr"]))
    for m in _PARA_RE.finditer(text):
        breaks.append((m.start(), _BREAK_SCORES["paragraph"]))

    # Filter out breaks inside code fences
    fence_positions = [m.start() for m in _FENCE_RE.finditer(text)]
    in_fence_ranges: List[Tuple[int, int]] = []
    for i in range(0, len(fence_positions) - 1, 2):
        in_fence_ranges.append((fence_positions[i], fence_positions[i + 1] if i + 1 < len(fence_positions) else len(text)))

    def _in_fence(pos: int) -> bool:
        return any(start <= pos <= end for start, end in in_fence_ranges)

    breaks = [(pos, score) for pos, score in breaks if not _in_fence(pos)]
    breaks.sort(key=lambda x: x[0])

    if not breaks:
        # No natural boundaries — hard split at max_chunk_size
        chunks = []
        for i in range(0, len(text), max_chunk_size - overlap):
            chunks.append(text[i:i + max_chunk_size])
        return chunks

    # Greedy chunking: pick best break point near max_chunk_size
    chunks = []
    start = 0

    while start < len(text):
        if len(text) - start <= max_chunk_size:
            chunks.append(text[start:])
            break

        # Find candidate breaks within [max_chunk_size * 0.5, max_chunk_size * 1.2]
        min_pos = start + int(max_chunk_size * 0.5)
        max_pos = start + int(max_chunk_size * 1.2)

        candidates = [
            (pos, score) for pos, score in breaks
            if min_pos <= pos <= max_pos
        ]

        if candidates:
            # Score with squared distance decay from ideal position
            ideal = start + max_chunk_size
            best_pos = max(
                candidates,
                key=lambda c: c[1] / max(1, ((c[0] - ideal) ** 2) / 10000 + 1)
            )[0]
            chunks.append(text[start:best_pos])
            # Guarantee forward progress: advance at least half of max_chunk_size
            new_start = max(best_pos - overlap, start + max(max_chunk_size // 2, 1))
            start = min(new_start, best_pos)  # But don't skip past the break
        else:
            # No good break — take max_chunk_size
            chunks.append(text[start:start + max_chunk_size])
            start += max_chunk_size - overlap

    return [c for c in chunks if c.strip()]


# ── Reciprocal Rank Fusion ─────────────────────────────────────

def reciprocal_rank_fusion(
    *ranked_lists: List[Tuple[str, float]],
    k: int = 60,
) -> List[Tuple[str, float]]:
    """
    Combine multiple ranked lists via RRF (k=60 default).

    Each input is a list of (id, original_score) tuples, ordered by rank.
    Returns combined list sorted by fused score.
    """
    scores: Dict[str, float] = {}
    for ranked_list in ranked_lists:
        for rank, (item_id, _) in enumerate(ranked_list):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)

    combined = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return combined


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

    def smart_search(
        self,
        query: str,
        limit: int = 10,
        rerank_fn: Optional[Callable] = None,
        vector_store: Optional[Any] = None,
    ) -> List[IndexResult]:
        """
        Three-tier search with Reciprocal Rank Fusion (C3).

        Tiers:
          1. BM25 (FTS5) — keyword relevance
          2. Vector similarity — semantic relevance (if vector_store provided)
          3. LLM reranking — precision boost (if rerank_fn provided)

        Combined via RRF(k=60). Falls back to single-tier if components unavailable.
        """
        # Tier 1: BM25 via FTS5
        bm25_results = self._bm25_search(query, limit=limit * 3)
        bm25_ranked = [(str(r.title), r.score) for r in bm25_results]

        # Tier 2: Vector similarity (optional)
        vector_ranked: List[Tuple[str, float]] = []
        if vector_store is not None:
            try:
                query_embedding = vector_store.compute_embedding(query)
                if query_embedding:
                    vec_results = vector_store.search(query_embedding, limit=limit * 3)
                    vector_ranked = [(eid, score) for eid, score in vec_results]
            except Exception as e:
                logger.debug(f"Vector search skipped: {e}")

        # Fuse BM25 + vector via RRF
        if vector_ranked:
            fused = reciprocal_rank_fusion(bm25_ranked, vector_ranked, k=60)
        else:
            fused = bm25_ranked

        # Map fused scores back to IndexResult objects
        result_map = {str(r.title): r for r in bm25_results}
        fused_results = []
        for item_id, rrf_score in fused[:limit * 2]:
            if item_id in result_map:
                r = result_map[item_id]
                r.score = rrf_score
                r.match_type = "hybrid" if vector_ranked else "fts"
                fused_results.append(r)

        # Tier 3: LLM reranking (optional)
        if rerank_fn and len(fused_results) > limit:
            try:
                reranked = rerank_fn(
                    query=query,
                    candidates=[{"title": r.title, "summary": r.summary[:200]} for r in fused_results],
                )
                if reranked and isinstance(reranked, list):
                    # rerank_fn returns indices or titles in reranked order
                    reranked_titles = set()
                    ordered = []
                    for item in reranked[:limit]:
                        title = item if isinstance(item, str) else item.get("title", "")
                        if title in result_map and title not in reranked_titles:
                            r = result_map[title]
                            r.match_type = "reranked"
                            ordered.append(r)
                            reranked_titles.add(title)
                    if ordered:
                        fused_results = ordered
            except Exception as e:
                logger.debug(f"LLM reranking skipped: {e}")

        return fused_results[:limit]

    def _bm25_search(self, query: str, limit: int = 30) -> List[IndexResult]:
        """Pure BM25 search via FTS5 — extracted for reuse in smart_search."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        results = []

        try:
            rows = conn.execute(
                """SELECT e.*, bm25(research_fts) as rank
                   FROM research_fts f
                   JOIN research_entries e ON f.rowid = e.id
                   WHERE research_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (query, limit),
            ).fetchall()

            for row in rows:
                score = -row["rank"]
                # Relevance + verification boosts
                if row["relevance"] == "high":
                    score *= 1.3
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
            # FTS5 match syntax error — LIKE fallback
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
        return results

    def add_chunked_document(
        self,
        title: str,
        content: str,
        source: str = "",
        tags: Optional[List[str]] = None,
        max_chunk_size: int = 800,
        context_annotation: str = "",
    ):
        """
        Smart-chunk a document and index each chunk with hierarchical context.

        Each chunk inherits the parent document's context_annotation,
        providing collection/path-level context when results are retrieved.
        """
        chunks = smart_chunk(content, max_chunk_size=max_chunk_size)

        for i, chunk in enumerate(chunks):
            chunk_title = f"{title} [chunk {i + 1}/{len(chunks)}]"
            summary = chunk[:500] if len(chunk) > 500 else chunk

            # Hierarchical context: prepend context annotation if provided
            if context_annotation:
                summary = f"[{context_annotation}] {summary}"

            self.add_finding(
                title=chunk_title,
                summary=summary,
                source=source,
                tags=tags,
                raw_content=chunk,
            )

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
