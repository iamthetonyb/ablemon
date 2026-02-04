"""
Hybrid Memory System
Combines structured SQLite storage with semantic vector embeddings.
Provides both exact and fuzzy retrieval for agent memory.
"""

import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum

class MemoryType(Enum):
    CONVERSATION = "conversation"
    LEARNING = "learning"
    OBJECTIVE = "objective"
    CLIENT_CONTEXT = "client_context"
    SKILL = "skill"
    AUDIT = "audit"

@dataclass
class MemoryEntry:
    """A single memory entry"""
    id: str
    content: str
    memory_type: MemoryType
    timestamp: datetime
    metadata: Dict[str, Any] = field(default_factory=dict)
    embedding: Optional[List[float]] = None
    client_id: Optional[str] = None
    session_id: Optional[str] = None
    relevance_score: float = 1.0

@dataclass
class SearchResult:
    """Result from memory search"""
    entry: MemoryEntry
    score: float
    match_type: str  # "exact", "semantic", "hybrid"

class HybridMemory:
    """
    Hybrid memory system combining:
    - SQLite for structured storage and exact search
    - Vector embeddings for semantic search
    - Integration with v1 (~/.atlas/memory) for shared persistence
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        embeddings_path: Optional[Path] = None,
        use_v1_bridge: bool = True
    ):
        from .db.sqlite_store import SQLiteStore
        from .embeddings.vector_store import VectorStore

        base_path = Path(__file__).parent

        self.db = SQLiteStore(db_path or base_path / "db" / "memory.db")
        self.vectors = VectorStore(embeddings_path or base_path / "embeddings" / "vectors.bin")

        self.use_v1_bridge = use_v1_bridge
        self._v1_path = Path.home() / ".atlas" / "memory" if use_v1_bridge else None

        # Cache for recent memories
        self._cache: Dict[str, MemoryEntry] = {}
        self._cache_max_size = 1000

    def _generate_id(self, content: str, memory_type: MemoryType) -> str:
        """Generate unique ID for memory entry"""
        timestamp = datetime.utcnow().isoformat()
        hash_input = f"{timestamp}:{memory_type.value}:{content[:100]}"
        return hashlib.sha256(hash_input.encode()).hexdigest()[:16]

    def store(
        self,
        content: str,
        memory_type: MemoryType,
        metadata: Dict[str, Any] = None,
        client_id: Optional[str] = None,
        session_id: Optional[str] = None,
        compute_embedding: bool = True
    ) -> str:
        """Store a memory entry"""
        entry_id = self._generate_id(content, memory_type)

        # Compute embedding for semantic search
        embedding = None
        if compute_embedding:
            embedding = self.vectors.compute_embedding(content)

        entry = MemoryEntry(
            id=entry_id,
            content=content,
            memory_type=memory_type,
            timestamp=datetime.utcnow(),
            metadata=metadata or {},
            embedding=embedding,
            client_id=client_id,
            session_id=session_id
        )

        # Store in SQLite
        self.db.insert(entry)

        # Store embedding in vector store
        if embedding:
            self.vectors.add(entry_id, embedding, {
                "type": memory_type.value,
                "client_id": client_id
            })

        # Update cache
        self._cache[entry_id] = entry
        if len(self._cache) > self._cache_max_size:
            # Remove oldest entries
            oldest = sorted(self._cache.items(), key=lambda x: x[1].timestamp)[:100]
            for old_id, _ in oldest:
                del self._cache[old_id]

        # Sync to v1 if bridge enabled
        if self.use_v1_bridge and memory_type == MemoryType.LEARNING:
            self._sync_to_v1_learnings(entry)

        return entry_id

    def retrieve(self, entry_id: str) -> Optional[MemoryEntry]:
        """Retrieve a specific memory entry"""
        if entry_id in self._cache:
            return self._cache[entry_id]
        return self.db.get(entry_id)

    def search(
        self,
        query: str,
        memory_types: List[MemoryType] = None,
        client_id: Optional[str] = None,
        limit: int = 10,
        min_score: float = 0.5
    ) -> List[SearchResult]:
        """
        Hybrid search combining exact and semantic matching.
        Returns results ranked by combined score.
        """
        results = []

        # Exact search in SQLite (keyword matching)
        exact_results = self.db.search(
            query=query,
            memory_types=memory_types,
            client_id=client_id,
            limit=limit * 2  # Get more for re-ranking
        )

        for entry, score in exact_results:
            results.append(SearchResult(
                entry=entry,
                score=score,
                match_type="exact"
            ))

        # Semantic search via embeddings
        query_embedding = self.vectors.compute_embedding(query)
        if query_embedding:
            semantic_results = self.vectors.search(
                query_embedding,
                limit=limit * 2,
                filter_metadata={"client_id": client_id} if client_id else None
            )

            for entry_id, score in semantic_results:
                if score >= min_score:
                    entry = self.retrieve(entry_id)
                    if entry and (not memory_types or entry.memory_type in memory_types):
                        # Check if already in results
                        existing = next((r for r in results if r.entry.id == entry_id), None)
                        if existing:
                            # Boost score for hybrid match
                            existing.score = min(1.0, existing.score + score * 0.5)
                            existing.match_type = "hybrid"
                        else:
                            results.append(SearchResult(
                                entry=entry,
                                score=score,
                                match_type="semantic"
                            ))

        # Sort by score and return top results
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:limit]

    def get_context_for_agent(
        self,
        objective: str,
        client_id: Optional[str] = None,
        max_tokens: int = 2000
    ) -> str:
        """
        Get relevant context for an agent given an objective.
        Combines recent memories, relevant learnings, and client context.
        """
        context_parts = []

        # Get relevant memories
        relevant = self.search(
            query=objective,
            client_id=client_id,
            limit=10
        )

        for result in relevant:
            entry = result.entry
            if entry.memory_type == MemoryType.LEARNING:
                context_parts.append(f"[Learning] {entry.content}")
            elif entry.memory_type == MemoryType.CLIENT_CONTEXT:
                context_parts.append(f"[Client Context] {entry.content}")
            elif entry.memory_type == MemoryType.OBJECTIVE:
                context_parts.append(f"[Related Objective] {entry.content}")

        # Get recent conversation context
        recent = self.db.get_recent(
            memory_type=MemoryType.CONVERSATION,
            client_id=client_id,
            limit=5
        )

        for entry in recent:
            context_parts.append(f"[Recent] {entry.content[:200]}...")

        # Combine and truncate to max_tokens (rough estimate: 4 chars per token)
        context = "\n".join(context_parts)
        max_chars = max_tokens * 4
        if len(context) > max_chars:
            context = context[:max_chars] + "..."

        return context

    def _sync_to_v1_learnings(self, entry: MemoryEntry):
        """Sync learning to v1 ~/.atlas/memory/learnings.md"""
        if not self._v1_path:
            return

        learnings_file = self._v1_path / "learnings.md"
        if learnings_file.exists():
            timestamp = entry.timestamp.strftime("%Y-%m-%d %H:%M")
            new_entry = f"\n### {timestamp}\n{entry.content}\n"

            with open(learnings_file, "a") as f:
                f.write(new_entry)

    def import_from_v1(self):
        """Import memories from v1 system"""
        if not self._v1_path or not self._v1_path.exists():
            return {"imported": 0, "errors": []}

        imported = 0
        errors = []

        # Import learnings
        learnings_file = self._v1_path / "learnings.md"
        if learnings_file.exists():
            content = learnings_file.read_text()
            # Parse markdown entries
            sections = content.split("\n### ")
            for section in sections[1:]:  # Skip header
                lines = section.strip().split("\n")
                if len(lines) >= 2:
                    timestamp_str = lines[0]
                    learning_content = "\n".join(lines[1:])
                    try:
                        self.store(
                            content=learning_content,
                            memory_type=MemoryType.LEARNING,
                            metadata={"source": "v1_import", "original_timestamp": timestamp_str}
                        )
                        imported += 1
                    except Exception as e:
                        errors.append(f"Learning import error: {e}")

        # Import objectives
        objectives_file = self._v1_path / "current_objectives.yaml"
        if objectives_file.exists():
            import yaml
            try:
                objectives = yaml.safe_load(objectives_file.read_text())
                for section in ["urgent", "this_week", "backlog"]:
                    for obj in objectives.get(section, []):
                        self.store(
                            content=obj.get("description", ""),
                            memory_type=MemoryType.OBJECTIVE,
                            metadata={"status": obj.get("status"), "source": "v1_import"}
                        )
                        imported += 1
            except Exception as e:
                errors.append(f"Objectives import error: {e}")

        return {"imported": imported, "errors": errors}

    def export_to_v1(self):
        """Export recent memories to v1 format"""
        if not self._v1_path:
            return

        # Export recent learnings
        learnings = self.db.get_recent(
            memory_type=MemoryType.LEARNING,
            limit=50
        )

        learnings_file = self._v1_path / "learnings.md"
        if learnings_file.exists():
            existing = learnings_file.read_text()
            existing_timestamps = set()
            for line in existing.split("\n"):
                if line.startswith("### "):
                    existing_timestamps.add(line[4:].strip())

            new_entries = []
            for entry in learnings:
                timestamp = entry.timestamp.strftime("%Y-%m-%d %H:%M")
                if timestamp not in existing_timestamps:
                    new_entries.append(f"\n### {timestamp}\n{entry.content}\n")

            if new_entries:
                with open(learnings_file, "a") as f:
                    f.writelines(new_entries)
