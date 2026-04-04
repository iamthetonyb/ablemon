"""
DPO Training Pair Builder — constructs chosen/rejected pairs for Direct
Preference Optimization (DPO) fine-tuning from the interaction log.

Three sources of signal:
  1. RLHF-negative / correction-detected interactions → rejected half.
     If feedback_text is present it becomes the "chosen" response.
  2. High-quality positive interactions (feedback_signal='positive' AND
     audit_score >= 4.0) → chosen half, paired with the lowest-scored
     response in the same domain+complexity band as the rejected sample.
  3. Audit-only: rows with audit_score < 3.0 are treated as rejected even
     without explicit feedback, paired against high-quality same-domain rows.

Output format per pair (ChatML-style prompt):
  {
    "prompt":               "<|im_start|>user\n{input}<|im_end|>\n<|im_start|>assistant\n",
    "chosen":               "good response text",
    "rejected":             "bad response text",
    "source":               "rlhf|audit|correction",
    "domain":               "coding|security|...",
    "audit_score_rejected": float,
    "audit_score_chosen":   float,
  }

Usage:
    builder = DPOBuilder()
    pairs   = builder.build_pairs(since_hours=24)
    count   = builder.export_jsonl()
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from able.core.routing.interaction_log import DEFAULT_DB_PATH, InteractionLogger

logger = logging.getLogger(__name__)

# Audit score floor for a row to qualify as a "chosen" sample.
_CHOSEN_SCORE_FLOOR = 4.0
# Audit score ceiling for a row to qualify as a "rejected" sample.
_REJECTED_SCORE_CEILING = 3.0
# Complexity bands for pairing — we match rejected rows to chosen rows in the
# same rough complexity band to keep pairs meaningful.
_COMPLEXITY_BANDS = [(0.0, 0.4), (0.4, 0.7), (0.7, 1.0)]

_DEFAULT_OUTPUT = "data/distillation_dpo.jsonl"


def _chatml_prompt(user_input: str) -> str:
    """Wrap user input in the ChatML assistant-turn prefix used during training."""
    return f"<|im_start|>user\n{user_input}<|im_end|>\n<|im_start|>assistant\n"


def _band_for(score: float) -> tuple:
    for lo, hi in _COMPLEXITY_BANDS:
        if lo <= score < hi:
            return (lo, hi)
    return _COMPLEXITY_BANDS[-1]


class DPOBuilder:
    """
    Build DPO training pairs from the interaction log.

    Args:
        db_path: Path to interaction_log.db.  Defaults to DEFAULT_DB_PATH.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        # Instantiate the logger to ensure schema migrations have been applied.
        _il = InteractionLogger(db_path)
        self._db_path = _il.db_path

    # ── DB helpers ────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _since_iso(self, hours: int) -> str:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        return cutoff.isoformat()

    # ── Query helpers ─────────────────────────────────────────────────────

    def _fetch_negative_interactions(
        self, since_iso: str
    ) -> List[Dict[str, Any]]:
        """Rows that had explicit negative feedback or a detected correction."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT *
                FROM interaction_log
                WHERE (feedback_signal = 'negative' OR correction_detected = 1)
                  AND raw_input IS NOT NULL
                  AND raw_output IS NOT NULL
                  AND timestamp >= ?
                ORDER BY timestamp DESC
                """,
                (since_iso,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def _fetch_high_quality_interactions(
        self, since_iso: str
    ) -> List[Dict[str, Any]]:
        """Rows with positive feedback AND a strong audit score."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT *
                FROM interaction_log
                WHERE feedback_signal = 'positive'
                  AND audit_score >= ?
                  AND raw_input IS NOT NULL
                  AND raw_output IS NOT NULL
                  AND timestamp >= ?
                ORDER BY audit_score DESC
                """,
                (_CHOSEN_SCORE_FLOOR, since_iso),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def _fetch_low_score_interactions(
        self, since_iso: str
    ) -> List[Dict[str, Any]]:
        """Rows with an audit score below the rejection ceiling (no explicit feedback needed)."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT *
                FROM interaction_log
                WHERE audit_score IS NOT NULL
                  AND audit_score < ?
                  AND raw_input IS NOT NULL
                  AND raw_output IS NOT NULL
                  AND timestamp >= ?
                ORDER BY audit_score ASC
                """,
                (_REJECTED_SCORE_CEILING, since_iso),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def _fetch_high_score_for_domain(
        self,
        domain: str,
        complexity_lo: float,
        complexity_hi: float,
        exclude_ids: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Find the best high-quality response in the same domain and complexity
        band, excluding any IDs already used.
        """
        exclude_ids = exclude_ids or []
        conn = self._connect()
        try:
            placeholders = ", ".join("?" * len(exclude_ids)) if exclude_ids else "NULL"
            exclude_clause = f"AND id NOT IN ({placeholders})" if exclude_ids else ""
            params: list = [
                _CHOSEN_SCORE_FLOOR,
                domain,
                complexity_lo,
                complexity_hi,
            ] + exclude_ids
            row = conn.execute(
                f"""
                SELECT *
                FROM interaction_log
                WHERE audit_score >= ?
                  AND raw_input IS NOT NULL
                  AND raw_output IS NOT NULL
                  AND domain = ?
                  AND complexity_score >= ?
                  AND complexity_score < ?
                  {exclude_clause}
                ORDER BY audit_score DESC
                LIMIT 1
                """,
                params,
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # ── Pair construction ─────────────────────────────────────────────────

    def _make_pair(
        self,
        prompt: str,
        chosen: str,
        rejected: str,
        source: str,
        domain: str,
        score_rejected: float,
        score_chosen: float,
    ) -> Dict[str, Any]:
        return {
            "prompt": _chatml_prompt(prompt),
            "chosen": chosen,
            "rejected": rejected,
            "source": source,
            "domain": domain,
            "audit_score_rejected": round(score_rejected, 3),
            "audit_score_chosen": round(score_chosen, 3),
        }

    def build_pairs(self, since_hours: int = 24) -> List[Dict[str, Any]]:
        """
        Build DPO training pairs from the last `since_hours` of interactions.

        Returns a list of pair dicts.
        """
        since_iso = self._since_iso(since_hours)
        pairs: List[Dict[str, Any]] = []
        used_chosen_ids: List[str] = []

        # ── Source 1: explicit RLHF negative / correction ─────────────────
        negatives = self._fetch_negative_interactions(since_iso)
        for row in negatives:
            raw_input: str = row.get("raw_input") or ""
            raw_output: str = row.get("raw_output") or ""
            feedback_text: Optional[str] = row.get("feedback_text")
            domain: str = row.get("domain") or "default"
            score_rejected = float(row.get("audit_score") or 0.0)
            source = "correction" if row.get("correction_detected") else "rlhf"

            if feedback_text:
                # User provided an explicit correction → that IS the chosen response
                pairs.append(
                    self._make_pair(
                        prompt=raw_input,
                        chosen=feedback_text,
                        rejected=raw_output,
                        source=source,
                        domain=domain,
                        score_rejected=score_rejected,
                        score_chosen=_CHOSEN_SCORE_FLOOR,  # assume user correction is good
                    )
                )
            else:
                # No explicit correction text — try to find a high-quality same-domain pair
                band = _band_for(float(row.get("complexity_score") or 0.0))
                chosen_row = self._fetch_high_score_for_domain(
                    domain, band[0], band[1], exclude_ids=used_chosen_ids
                )
                if chosen_row is None:
                    # Broaden: try any domain at same complexity band
                    chosen_row = self._fetch_high_score_for_domain(
                        "default", band[0], band[1], exclude_ids=used_chosen_ids
                    )
                if chosen_row is None:
                    logger.debug(
                        "No chosen candidate for rejected row %s (domain=%s) — skipping",
                        row["id"],
                        domain,
                    )
                    continue

                chosen_id: str = chosen_row["id"]
                used_chosen_ids.append(chosen_id)
                pairs.append(
                    self._make_pair(
                        prompt=raw_input,
                        chosen=chosen_row.get("raw_output") or "",
                        rejected=raw_output,
                        source=source,
                        domain=domain,
                        score_rejected=score_rejected,
                        score_chosen=float(chosen_row.get("audit_score") or _CHOSEN_SCORE_FLOOR),
                    )
                )

        # ── Source 2: audit-only low-score rows (no explicit feedback) ────
        low_scores = self._fetch_low_score_interactions(since_iso)
        # Deduplicate against rows already covered by RLHF negatives
        rlhf_ids = {r["id"] for r in negatives}
        for row in low_scores:
            if row["id"] in rlhf_ids:
                continue
            raw_input = row.get("raw_input") or ""
            raw_output = row.get("raw_output") or ""
            domain = row.get("domain") or "default"
            score_rejected = float(row.get("audit_score") or 0.0)

            band = _band_for(float(row.get("complexity_score") or 0.0))
            chosen_row = self._fetch_high_score_for_domain(
                domain, band[0], band[1], exclude_ids=used_chosen_ids
            )
            if chosen_row is None:
                logger.debug(
                    "No chosen candidate for audit-rejected row %s — skipping", row["id"]
                )
                continue

            chosen_id = chosen_row["id"]
            used_chosen_ids.append(chosen_id)
            pairs.append(
                self._make_pair(
                    prompt=raw_input,
                    chosen=chosen_row.get("raw_output") or "",
                    rejected=raw_output,
                    source="audit",
                    domain=domain,
                    score_rejected=score_rejected,
                    score_chosen=float(chosen_row.get("audit_score") or _CHOSEN_SCORE_FLOOR),
                )
            )

        logger.info(
            "DPO pair build complete: %d pairs from last %dh "
            "(rlhf_negatives=%d, audit_rejected=%d)",
            len(pairs),
            since_hours,
            len(negatives),
            len(low_scores),
        )
        return pairs

    def export_jsonl(
        self,
        output_path: str = _DEFAULT_OUTPUT,
        since_hours: int = 24,
    ) -> int:
        """
        Build pairs and write them to a JSONL file.

        Each line is a JSON object.  Returns the number of pairs written.
        """
        pairs = self.build_pairs(since_hours=since_hours)
        if not pairs:
            logger.info("DPO export: no pairs to write")
            return 0

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        written = 0
        with out.open("a", encoding="utf-8") as fh:
            for pair in pairs:
                fh.write(json.dumps(pair, ensure_ascii=False) + "\n")
                written += 1

        logger.info("DPO export: wrote %d pairs to %s", written, output_path)
        return written
