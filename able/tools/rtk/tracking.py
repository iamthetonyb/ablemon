"""
D2 — RTK Token Savings Analytics.

Tracks compression savings over time for cost-aware routing optimization.
SQLite-backed for persistence across sessions.

Source: Ported from rtk-ai/rtk src/core/tracking.rs
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class CompressionStats:
    """Aggregated compression statistics."""
    total_commands: int
    total_original_tokens: int
    total_compressed_tokens: int
    total_savings_pct: float
    top_commands: list[dict]  # [{"command": str, "avg_savings": float, "count": int}]


class RTKTracker:
    """Track RTK compression savings in SQLite.

    Usage::

        tracker = RTKTracker()
        tracker.record("git status", original_tokens=500, compressed_tokens=50)
        stats = tracker.get_stats()
        print(f"Average savings: {stats.total_savings_pct:.1%}")
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        if db_path is None:
            db_dir = Path.home() / ".able" / "data"
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(db_dir / "rtk_tracking.db")
        self._db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rtk_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    command_prefix TEXT NOT NULL,
                    original_tokens INTEGER NOT NULL,
                    compressed_tokens INTEGER NOT NULL,
                    savings_pct REAL NOT NULL,
                    execution_time_ms INTEGER DEFAULT 0,
                    timestamp REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_rtk_timestamp
                ON rtk_events(timestamp)
            """)

    def record(self, command: str, original_tokens: int,
               compressed_tokens: int,
               execution_time_ms: int = 0) -> None:
        """Record a compression event."""
        # Extract command prefix (first 2 words) for grouping
        parts = command.strip().split()
        prefix = " ".join(parts[:2]) if len(parts) >= 2 else command.strip()
        prefix = prefix[:100]  # Cap length

        savings = (
            1.0 - (compressed_tokens / original_tokens)
            if original_tokens > 0
            else 0.0
        )

        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO rtk_events
                   (command_prefix, original_tokens, compressed_tokens,
                    savings_pct, execution_time_ms, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (prefix, original_tokens, compressed_tokens,
                 savings, execution_time_ms, time.time()),
            )

    def get_stats(self, since_hours: int = 24) -> CompressionStats:
        """Get aggregated compression stats."""
        cutoff = time.time() - (since_hours * 3600)

        with sqlite3.connect(self._db_path) as conn:
            # Overall stats
            row = conn.execute(
                """SELECT COUNT(*), COALESCE(SUM(original_tokens), 0),
                          COALESCE(SUM(compressed_tokens), 0)
                   FROM rtk_events WHERE timestamp > ?""",
                (cutoff,),
            ).fetchone()

            total = row[0]
            orig = row[1]
            comp = row[2]
            pct = 1.0 - (comp / orig) if orig > 0 else 0.0

            # Top commands by savings
            top_rows = conn.execute(
                """SELECT command_prefix,
                          AVG(savings_pct) as avg_savings,
                          COUNT(*) as cnt
                   FROM rtk_events
                   WHERE timestamp > ?
                   GROUP BY command_prefix
                   ORDER BY avg_savings DESC
                   LIMIT 10""",
                (cutoff,),
            ).fetchall()

            top = [
                {"command": r[0], "avg_savings": r[1], "count": r[2]}
                for r in top_rows
            ]

            return CompressionStats(
                total_commands=total,
                total_original_tokens=orig,
                total_compressed_tokens=comp,
                total_savings_pct=pct,
                top_commands=top,
            )
