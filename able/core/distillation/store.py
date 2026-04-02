"""SQLite store for distillation pipeline data."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from able.core.distillation.models import (
    ConversationRecord,
    CorpusTier,
    DistillationPair,
    TrainingPair,
    ThinkingTrace,
)

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "data/distillation.db"


class DistillationStore:
    """SQLite store for distillation pipeline data.

    Uses WAL mode for concurrent reads.  Follows the same patterns as
    ``able.core.routing.interaction_log.InteractionLogger``.
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS conversation_records (
        id TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        messages TEXT NOT NULL,
        model TEXT NOT NULL,
        tier INTEGER NOT NULL,
        domain TEXT NOT NULL,
        quality_score REAL DEFAULT 0.0,
        thinking_trace TEXT,
        tenant_id TEXT DEFAULT 'default',
        timestamp TEXT NOT NULL,
        metadata TEXT,
        content_hash TEXT NOT NULL
    );

    CREATE UNIQUE INDEX IF NOT EXISTS idx_cr_content_hash
        ON conversation_records(content_hash);
    CREATE INDEX IF NOT EXISTS idx_cr_tenant_id
        ON conversation_records(tenant_id);
    CREATE INDEX IF NOT EXISTS idx_cr_domain
        ON conversation_records(domain);
    CREATE INDEX IF NOT EXISTS idx_cr_quality_score
        ON conversation_records(quality_score);
    CREATE INDEX IF NOT EXISTS idx_cr_timestamp
        ON conversation_records(timestamp);

    CREATE TABLE IF NOT EXISTS distillation_pairs (
        id TEXT PRIMARY KEY,
        prompt TEXT NOT NULL,
        gold_response TEXT NOT NULL,
        gold_model TEXT NOT NULL,
        gold_thinking TEXT,
        domain TEXT NOT NULL,
        quality_score REAL DEFAULT 0.0,
        tenant_id TEXT DEFAULT 'default',
        corpus_version TEXT,
        tags TEXT,
        created_at TEXT NOT NULL,
        content_hash TEXT NOT NULL
    );

    CREATE UNIQUE INDEX IF NOT EXISTS idx_dp_content_hash
        ON distillation_pairs(content_hash);
    CREATE INDEX IF NOT EXISTS idx_dp_tenant_id
        ON distillation_pairs(tenant_id);
    CREATE INDEX IF NOT EXISTS idx_dp_domain
        ON distillation_pairs(domain);
    CREATE INDEX IF NOT EXISTS idx_dp_quality_score
        ON distillation_pairs(quality_score);
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
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

    # ── Serialization helpers ──────────────────────────────────

    @staticmethod
    def _serialize_thinking(trace: Optional[ThinkingTrace]) -> Optional[str]:
        if trace is None:
            return None
        return json.dumps(
            {
                "model": trace.model,
                "raw_thinking": trace.raw_thinking,
                "stripped_output": trace.stripped_output,
                "extraction_method": trace.extraction_method,
            }
        )

    @staticmethod
    def _deserialize_thinking(raw: Optional[str]) -> Optional[ThinkingTrace]:
        if raw is None:
            return None
        d = json.loads(raw)
        return ThinkingTrace(
            model=d["model"],
            raw_thinking=d["raw_thinking"],
            stripped_output=d["stripped_output"],
            extraction_method=d.get("extraction_method", "regex"),
        )

    # ── Write operations ───────────────────────────────────────

    def save_record(self, record: ConversationRecord) -> bool:
        """Save a conversation record. Returns False if duplicate (by content_hash)."""
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO conversation_records (
                    id, source, messages, model, tier, domain, quality_score,
                    thinking_trace, tenant_id, timestamp, metadata, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.id,
                    record.source,
                    json.dumps(record.messages),
                    record.model,
                    record.tier,
                    record.domain,
                    record.quality_score,
                    self._serialize_thinking(record.thinking_trace),
                    record.tenant_id,
                    record.timestamp.isoformat(),
                    json.dumps(record.metadata),
                    record.content_hash,
                ),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()

    def save_pair(self, pair: DistillationPair) -> bool:
        """Save a distillation pair. Returns False if duplicate."""
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO distillation_pairs (
                    id, prompt, gold_response, gold_model, gold_thinking,
                    domain, quality_score, tenant_id, corpus_version,
                    tags, created_at, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pair.id,
                    pair.prompt,
                    pair.gold_response,
                    pair.gold_model,
                    pair.gold_thinking,
                    pair.domain,
                    pair.quality_score,
                    pair.tenant_id,
                    pair.corpus_version,
                    json.dumps(pair.tags),
                    pair.created_at.isoformat(),
                    pair.content_hash,
                ),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()

    def save_training_pair(self, pair: TrainingPair) -> bool:
        """Persist the canonical pair type through the store schema."""
        return self.save_pair(pair.to_distillation_pair())

    # ── Read operations ────────────────────────────────────────

    def _row_to_record(self, row: sqlite3.Row) -> ConversationRecord:
        return ConversationRecord(
            id=row["id"],
            source=row["source"],
            messages=json.loads(row["messages"]),
            model=row["model"],
            tier=row["tier"],
            domain=row["domain"],
            quality_score=row["quality_score"],
            thinking_trace=self._deserialize_thinking(row["thinking_trace"]),
            tenant_id=row["tenant_id"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            content_hash=row["content_hash"],
        )

    def _row_to_pair(self, row: sqlite3.Row) -> DistillationPair:
        return DistillationPair(
            id=row["id"],
            prompt=row["prompt"],
            gold_response=row["gold_response"],
            gold_model=row["gold_model"],
            gold_thinking=row["gold_thinking"],
            domain=row["domain"],
            quality_score=row["quality_score"],
            tenant_id=row["tenant_id"],
            corpus_version=row["corpus_version"],
            tags=json.loads(row["tags"]) if row["tags"] else [],
            created_at=datetime.fromisoformat(row["created_at"]),
            content_hash=row["content_hash"],
        )

    def get_records(
        self,
        domain: Optional[str] = None,
        min_quality: float = 0.0,
        tenant_id: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 1000,
    ) -> List[ConversationRecord]:
        """Query conversation records with filters."""
        clauses: List[str] = ["quality_score >= ?"]
        params: list = [min_quality]

        if domain is not None:
            clauses.append("domain = ?")
            params.append(domain)
        if tenant_id is not None:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(since.isoformat())

        where = " AND ".join(clauses)
        params.append(limit)

        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT * FROM conversation_records WHERE {where} "
                f"ORDER BY timestamp DESC LIMIT ?",
                params,
            ).fetchall()
            return [self._row_to_record(r) for r in rows]
        finally:
            conn.close()

    def get_pairs(
        self,
        domain: Optional[str] = None,
        min_quality: float = 0.0,
        tenant_id: Optional[str] = None,
        corpus_version: Optional[str] = None,
        limit: int = 1000,
    ) -> List[DistillationPair]:
        """Query distillation pairs with filters."""
        clauses: List[str] = ["quality_score >= ?"]
        params: list = [min_quality]

        if domain is not None:
            clauses.append("domain = ?")
            params.append(domain)
        if tenant_id is not None:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if corpus_version is not None:
            clauses.append("corpus_version = ?")
            params.append(corpus_version)

        where = " AND ".join(clauses)
        params.append(limit)

        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT * FROM distillation_pairs WHERE {where} "
                f"ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
            return [self._row_to_pair(r) for r in rows]
        finally:
            conn.close()

    # ── Export ──────────────────────────────────────────────────

    def export_jsonl(
        self,
        output_path: str,
        domain: Optional[str] = None,
        min_quality: float = 0.8,
        tenant_id: Optional[str] = None,
        system_prompt: str = "",
    ) -> int:
        """Export pairs as JSONL for training. Returns count exported."""
        pairs = self.get_pairs(
            domain=domain,
            min_quality=min_quality,
            tenant_id=tenant_id,
            limit=1_000_000,
        )

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            for pair in pairs:
                f.write(json.dumps(pair.to_chatml(system_prompt)) + "\n")

        return len(pairs)

    # ── Stats ──────────────────────────────────────────────────

    def count(
        self,
        table: str = "conversation_records",
        tenant_id: Optional[str] = None,
    ) -> int:
        """Count records in a table."""
        if table not in ("conversation_records", "distillation_pairs"):
            raise ValueError(f"Unknown table: {table}")

        conn = self._connect()
        try:
            if tenant_id is not None:
                row = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE tenant_id = ?",
                    (tenant_id,),
                ).fetchone()
            else:
                row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            return row[0]
        finally:
            conn.close()

    @staticmethod
    def _corpus_tier_from_count(pair_count: int) -> Optional[CorpusTier]:
        """Map a pair count to the corresponding corpus tier."""
        if pair_count >= 10_000:
            return CorpusTier.FULL
        if pair_count >= 2_000:
            return CorpusTier.GROWTH
        if pair_count >= 500:
            return CorpusTier.SEED
        return None

    def get_corpus_tier(self, tenant_id: Optional[str] = None) -> Optional[CorpusTier]:
        """Determine current corpus tier based on pair count."""
        return self._corpus_tier_from_count(
            self.count("distillation_pairs", tenant_id)
        )

    def stats(self, tenant_id: Optional[str] = None) -> Dict:
        """Get pipeline stats: totals, by-domain, by-model, quality distribution, corpus tier."""
        conn = self._connect()
        try:
            tenant_filter = ""
            tenant_and = ""
            params: tuple = ()
            if tenant_id is not None:
                tenant_filter = " WHERE tenant_id = ?"
                tenant_and = " AND tenant_id = ?"
                params = (tenant_id,)

            total_records = conn.execute(
                f"SELECT COUNT(*) FROM conversation_records{tenant_filter}",
                params,
            ).fetchone()[0]

            total_pairs = conn.execute(
                f"SELECT COUNT(*) FROM distillation_pairs{tenant_filter}",
                params,
            ).fetchone()[0]

            # By domain
            rows = conn.execute(
                f"SELECT domain, COUNT(*) as cnt FROM distillation_pairs{tenant_filter} GROUP BY domain",
                params,
            ).fetchall()
            by_domain = {r["domain"]: r["cnt"] for r in rows}

            # By model
            rows = conn.execute(
                f"SELECT gold_model, COUNT(*) as cnt FROM distillation_pairs{tenant_filter} GROUP BY gold_model",
                params,
            ).fetchall()
            by_model = {r["gold_model"]: r["cnt"] for r in rows}

            # Quality distribution (buckets: 0-0.2, 0.2-0.4, ..., 0.8-1.0)
            quality_dist: Dict[str, int] = {}
            for low in (0.0, 0.2, 0.4, 0.6, 0.8):
                high = low + 0.2
                label = f"{low:.1f}-{high:.1f}"
                q_sql = (
                    f"SELECT COUNT(*) FROM distillation_pairs "
                    f"WHERE quality_score >= ? AND quality_score < ?{tenant_and}"
                )
                row = conn.execute(q_sql, (low, high) + params).fetchone()
                quality_dist[label] = row[0]

            corpus_tier = self._corpus_tier_from_count(total_pairs)

            return {
                "total_records": total_records,
                "total_pairs": total_pairs,
                "by_domain": by_domain,
                "by_model": by_model,
                "quality_distribution": quality_dist,
                "corpus_tier": corpus_tier.value if corpus_tier else None,
            }
        finally:
            conn.close()

    # ── Retroactive scrubbing ────────────────────────────────────

    def scrub_corpus(self) -> Dict:
        """Retroactively strip scaffolding artifacts from all existing pairs.

        Applies the same ``_strip_scaffolding()`` logic used by harvesters
        to every prompt and gold_response already in the store.  This ensures
        old data collected before the filter was added gets cleaned too.

        Returns a dict with counts: ``{"scrubbed": N, "deleted": M}``.
        ``deleted`` counts pairs that became empty after stripping.
        """
        from able.core.distillation.harvesters.base import BaseHarvester

        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, prompt, gold_response, gold_thinking FROM distillation_pairs"
            ).fetchall()

            scrubbed = 0
            deleted = 0
            for row in rows:
                prompt = BaseHarvester._strip_scaffolding(row["prompt"])
                response = BaseHarvester._strip_scaffolding(row["gold_response"])
                thinking = row["gold_thinking"]
                if thinking:
                    thinking = BaseHarvester._strip_scaffolding(thinking)

                # If stripping emptied the pair, remove it
                if not prompt.strip() or not response.strip():
                    conn.execute(
                        "DELETE FROM distillation_pairs WHERE id = ?",
                        (row["id"],),
                    )
                    deleted += 1
                    continue

                # Only update if content actually changed
                if (prompt != row["prompt"]
                        or response != row["gold_response"]
                        or thinking != row["gold_thinking"]):
                    conn.execute(
                        "UPDATE distillation_pairs "
                        "SET prompt = ?, gold_response = ?, gold_thinking = ? "
                        "WHERE id = ?",
                        (prompt, response, thinking, row["id"]),
                    )
                    scrubbed += 1

            conn.commit()
            logger.info(
                "[scrub] Scrubbed %d pairs, deleted %d empty pairs",
                scrubbed, deleted,
            )
            return {"scrubbed": scrubbed, "deleted": deleted}
        finally:
            conn.close()

    @property
    def db_path(self) -> str:
        return self._db_path
