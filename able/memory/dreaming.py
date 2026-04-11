"""
Memory Dreaming / REM Cycle — Offline memory consolidation.

Runs during nightly cron to:
1. Scan recent conversation memories for durable facts
2. Extract entity-relation-entity triples → feed to temporal_graph.py
3. Detect and merge near-duplicate memories (cosine similarity > 0.9)
4. Prune stale facts not accessed in N days (archive, not delete)
5. Regenerate L1 summary for layered_memory.py

Forked from OpenClaw v4.6 offline memory consolidation pattern (C5 in master plan).
Wire into: able/scheduler/cron.py — nightly at 3am after distillation harvest.

Usage:
    cycle = REMCycle()
    report = await cycle.run()
    print(report.summary())
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Entity-relation extraction patterns
# Matches: "X is Y", "X has Y", "X uses Y", "X works on Y"
_RELATION_PATTERNS = [
    re.compile(r"(\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)*)\s+(?:is|are)\s+(?:a\s+)?(.+?)(?:\.|,|$)", re.MULTILINE),
    re.compile(r"(\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)*)\s+(?:uses?|prefers?)\s+(.+?)(?:\.|,|$)", re.MULTILINE),
    re.compile(r"(\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)*)\s+(?:works?\s+(?:on|with|at))\s+(.+?)(?:\.|,|$)", re.MULTILINE),
    re.compile(r"(\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)*)'s\s+(\w+)\s+is\s+(.+?)(?:\.|,|$)", re.MULTILINE),
]

# Key-value extraction: "rate: $150/hr", "stack: Python + React"
_KV_PATTERN = re.compile(
    r"(?:^|\n)\s*(?:\*\s*)?(\w[\w\s]{1,30}):\s+(.+)"
)


@dataclass
class ExtractedFact:
    """A fact extracted from conversation text."""
    subject: str
    predicate: str
    object: str
    source: str = ""
    confidence: float = 0.5


@dataclass
class DuplicatePair:
    """Two memory entries detected as near-duplicates."""
    entry_a_id: str
    entry_b_id: str
    similarity: float
    merged: bool = False


@dataclass
class REMReport:
    """Report from a REM cycle run."""
    facts_extracted: int = 0
    facts_added: int = 0
    duplicates_found: int = 0
    duplicates_merged: int = 0
    stale_pruned: int = 0
    l1_regenerated: bool = False
    duration_ms: float = 0
    errors: List[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"REM cycle: {self.facts_extracted} facts extracted, "
            f"{self.facts_added} added to graph, "
            f"{self.duplicates_merged}/{self.duplicates_found} duplicates merged, "
            f"{self.stale_pruned} stale pruned, "
            f"L1={'yes' if self.l1_regenerated else 'no'}, "
            f"{self.duration_ms:.0f}ms"
        )


class REMCycle:
    """
    Offline memory consolidation — runs nightly.

    Scans recent memories, extracts durable facts into the temporal
    knowledge graph, merges duplicates, and prunes stale entries.
    """

    def __init__(
        self,
        graph=None,
        memory=None,
        stale_days: int = 90,
        similarity_threshold: float = 0.9,
    ):
        self._graph = graph
        self._memory = memory
        self._stale_days = stale_days
        self._similarity_threshold = similarity_threshold

    def _get_graph(self):
        """Lazy-load temporal knowledge graph."""
        if self._graph is None:
            from able.memory.temporal_graph import TemporalKnowledgeGraph
            self._graph = TemporalKnowledgeGraph()
        return self._graph

    def _get_memory(self):
        """Lazy-load hybrid memory."""
        if self._memory is None:
            try:
                from able.memory.hybrid_memory import HybridMemory
                self._memory = HybridMemory()
            except Exception:
                logger.warning("HybridMemory unavailable — dreaming will skip memory scan")
        return self._memory

    async def run(self, since_hours: int = 24) -> REMReport:
        """Execute a full REM cycle.

        Args:
            since_hours: Look back this many hours for recent memories.

        Returns:
            REMReport with stats.
        """
        import time
        start = time.perf_counter()
        report = REMReport()

        # Phase 1: Extract facts from recent memories
        try:
            facts = self._extract_facts_from_recent(since_hours)
            report.facts_extracted = len(facts)
        except Exception as e:
            report.errors.append(f"Fact extraction failed: {e}")
            facts = []

        # Phase 2: Feed facts into temporal knowledge graph
        graph = self._get_graph()
        for fact in facts:
            try:
                graph.add_triple(
                    fact.subject,
                    fact.predicate,
                    fact.object,
                    metadata={"source": fact.source, "confidence": fact.confidence},
                )
                report.facts_added += 1
            except Exception as e:
                report.errors.append(f"Triple add failed: {e}")

        # Phase 3: Detect and merge duplicates
        try:
            dups = self._find_duplicates()
            report.duplicates_found = len(dups)
            for dup in dups:
                if self._merge_duplicate(dup):
                    report.duplicates_merged += 1
                    dup.merged = True
        except Exception as e:
            report.errors.append(f"Duplicate detection failed: {e}")

        # Phase 4: Prune stale facts
        try:
            report.stale_pruned = graph.prune_stale(days=self._stale_days)
        except Exception as e:
            report.errors.append(f"Stale pruning failed: {e}")

        # Phase 5: Regenerate L1 summary
        try:
            report.l1_regenerated = self._regenerate_l1()
        except Exception as e:
            report.errors.append(f"L1 regeneration failed: {e}")

        report.duration_ms = (time.perf_counter() - start) * 1000
        logger.info("REM cycle complete: %s", report.summary())
        return report

    # ── Phase 1: Fact extraction ──────────────────────────────────

    def _extract_facts_from_recent(self, since_hours: int) -> List[ExtractedFact]:
        """Scan recent memories and extract entity-relation triples."""
        memory = self._get_memory()
        if memory is None:
            return []

        facts = []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)

        try:
            recent = memory.db.get_recent(limit=100)
        except Exception:
            recent = []

        for entry in recent:
            content = entry.get("content", "") if isinstance(entry, dict) else getattr(entry, "content", "")
            timestamp = entry.get("timestamp", "") if isinstance(entry, dict) else getattr(entry, "timestamp", "")

            # Skip old entries
            if timestamp and isinstance(timestamp, str):
                try:
                    ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    if ts < cutoff:
                        continue
                except (ValueError, TypeError):
                    pass

            extracted = self.extract_facts(content)
            source_id = entry.get("id", "") if isinstance(entry, dict) else getattr(entry, "id", "")
            for fact in extracted:
                fact.source = str(source_id)
            facts.extend(extracted)

        return facts

    @staticmethod
    def extract_facts(text: str) -> List[ExtractedFact]:
        """Extract entity-relation-entity triples from text.

        Uses pattern matching (not LLM) for speed and determinism.
        Handles:
        - "X is Y" / "X are Y" patterns
        - "X uses Y" / "X prefers Y"
        - "X works on/with Y"
        - "X's Y is Z" (possessive predicate)
        - "key: value" patterns
        """
        if not text:
            return []

        facts = []
        seen = set()

        # Relation patterns
        for pattern in _RELATION_PATTERNS:
            for match in pattern.finditer(text):
                groups = match.groups()
                if len(groups) == 3:
                    # Possessive: "X's Y is Z"
                    subject = groups[0].strip()
                    predicate = groups[1].strip()
                    obj = groups[2].strip()[:200]
                elif len(groups) == 2:
                    subject = groups[0].strip()
                    # Infer predicate from the verb in the pattern
                    full_match = match.group(0)
                    if " is " in full_match or " are " in full_match:
                        predicate = "is"
                    elif " uses " in full_match or " use " in full_match:
                        predicate = "uses"
                    elif " prefers " in full_match or " prefer " in full_match:
                        predicate = "prefers"
                    elif " works " in full_match:
                        predicate = "works_with"
                    else:
                        predicate = "related_to"
                    obj = groups[1].strip()[:200]
                else:
                    continue

                key = (subject.lower(), predicate.lower(), obj.lower()[:50])
                if key not in seen and len(subject) > 1 and len(obj) > 1:
                    seen.add(key)
                    facts.append(ExtractedFact(
                        subject=subject,
                        predicate=predicate,
                        object=obj,
                        confidence=0.6,
                    ))

        # Key-value patterns
        for match in _KV_PATTERN.finditer(text):
            key = match.group(1).strip()
            value = match.group(2).strip()[:200]
            if len(key) > 1 and len(value) > 1:
                dedup_key = (key.lower(), "has", value.lower()[:50])
                if dedup_key not in seen:
                    seen.add(dedup_key)
                    facts.append(ExtractedFact(
                        subject=key,
                        predicate="has",
                        object=value,
                        confidence=0.5,
                    ))

        return facts

    # ── Phase 3: Duplicate detection ──────────────────────────────

    def _find_duplicates(self) -> List[DuplicatePair]:
        """Find near-duplicate triples in the knowledge graph.

        Uses exact subject+predicate matching with fuzzy object comparison.
        """
        graph = self._get_graph()
        stats = graph.stats()
        if stats["current_triples"] < 2:
            return []

        duplicates = []
        # Group by (subject, predicate) and compare objects
        conn = graph._connect()
        try:
            rows = conn.execute(
                "SELECT id, subject, predicate, object FROM triples "
                "WHERE valid_to IS NULL ORDER BY subject, predicate"
            ).fetchall()
        finally:
            conn.close()

        # Group by (subject, predicate)
        groups: Dict[Tuple[str, str], List] = {}
        for row in rows:
            key = (row["subject"], row["predicate"])
            groups.setdefault(key, []).append(row)

        for key, group in groups.items():
            if len(group) < 2:
                continue
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    sim = self._text_similarity(group[i]["object"], group[j]["object"])
                    if sim >= self._similarity_threshold:
                        duplicates.append(DuplicatePair(
                            entry_a_id=str(group[i]["id"]),
                            entry_b_id=str(group[j]["id"]),
                            similarity=sim,
                        ))
        return duplicates

    def _merge_duplicate(self, dup: DuplicatePair) -> bool:
        """Merge a duplicate pair by keeping the richer (longer) version."""
        graph = self._get_graph()
        conn = graph._connect()
        try:
            a = conn.execute("SELECT * FROM triples WHERE id = ?", (int(dup.entry_a_id),)).fetchone()
            b = conn.execute("SELECT * FROM triples WHERE id = ?", (int(dup.entry_b_id),)).fetchone()
            if not a or not b:
                return False

            # Keep the one with more content (longer object)
            keep_id = int(dup.entry_a_id) if len(a["object"]) >= len(b["object"]) else int(dup.entry_b_id)
            remove_id = int(dup.entry_b_id) if keep_id == int(dup.entry_a_id) else int(dup.entry_a_id)

            # Invalidate (not delete) the shorter one
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE triples SET valid_to = ? WHERE id = ? AND valid_to IS NULL",
                (now, remove_id),
            )
            conn.commit()
            return True
        except Exception as e:
            logger.debug("Merge failed: %s", e)
            return False
        finally:
            conn.close()

    @staticmethod
    def _text_similarity(a: str, b: str) -> float:
        """Simple character-level Jaccard similarity.

        Fast approximation — good enough for dedup. For semantic similarity
        (cosine on embeddings), use the vector store layer.
        """
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0
        # Character trigram Jaccard
        def trigrams(s: str) -> set:
            s = s.lower().strip()
            return {s[i:i+3] for i in range(max(len(s) - 2, 1))}

        set_a = trigrams(a)
        set_b = trigrams(b)
        if not set_a or not set_b:
            return 0.0
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return intersection / union if union > 0 else 0.0

    # ── Phase 5: L1 summary regeneration ──────────────────────────

    def _regenerate_l1(self) -> bool:
        """Regenerate the L1 essential summary from current graph state.

        L1 is a compact (~500-800 token) summary of top facts/learnings
        loaded automatically on session wake-up.
        """
        graph = self._get_graph()
        stats = graph.stats()
        if stats["current_triples"] == 0:
            return False

        try:
            from able.memory.layered_memory import LayeredMemory
            lm = LayeredMemory()
        except Exception:
            return False

        # Build L1 from top facts (most connected entities)
        conn = graph._connect()
        try:
            # Get most-referenced subjects (entities with most facts)
            rows = conn.execute(
                "SELECT subject, COUNT(*) as cnt FROM triples "
                "WHERE valid_to IS NULL "
                "GROUP BY subject ORDER BY cnt DESC LIMIT 10"
            ).fetchall()

            if not rows:
                return False

            l1_lines = []
            for row in rows:
                facts = graph.query_entity(row["subject"])
                for fact in facts[:3]:
                    l1_lines.append(f"- {fact.subject} {fact.predicate}: {fact.object}")

            l1_text = "\n".join(l1_lines[:20])
            lm.update_layer("l1", l1_text)
            return True
        except Exception as e:
            logger.debug("L1 regeneration failed: %s", e)
            return False
        finally:
            conn.close()
