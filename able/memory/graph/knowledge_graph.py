"""
Knowledge Graph Memory - Graph-based memory for interconnected entities.

Stores memories as nodes with typed relationships.
Supports graph traversal queries for context retrieval.

Architecture:
    Entity Node → Relationship Edge → Entity Node
    (with embeddings for semantic search)
"""

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
import hashlib

logger = logging.getLogger(__name__)


class EntityType(Enum):
    """Types of entities in the knowledge graph"""
    PERSON = "person"
    ORGANIZATION = "organization"
    CONCEPT = "concept"
    EVENT = "event"
    LOCATION = "location"
    TASK = "task"
    SKILL = "skill"
    DOCUMENT = "document"
    CONVERSATION = "conversation"
    MEMORY = "memory"
    CUSTOM = "custom"


class RelationType(Enum):
    """Types of relationships between entities"""
    # Hierarchical
    IS_A = "is_a"
    PART_OF = "part_of"
    CONTAINS = "contains"
    BELONGS_TO = "belongs_to"

    # Associations
    RELATED_TO = "related_to"
    SIMILAR_TO = "similar_to"
    DEPENDS_ON = "depends_on"

    # Actions
    CREATED = "created"
    MODIFIED = "modified"
    REFERENCED = "referenced"
    MENTIONED = "mentioned"

    # Temporal
    BEFORE = "before"
    AFTER = "after"
    DURING = "during"

    # Social
    KNOWS = "knows"
    WORKS_WITH = "works_with"
    REPORTS_TO = "reports_to"

    # Custom
    CUSTOM = "custom"


@dataclass
class Entity:
    """A node in the knowledge graph"""
    id: str
    name: str
    entity_type: EntityType
    properties: Dict[str, Any] = field(default_factory=dict)
    embedding: Optional[List[float]] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    access_count: int = 0
    last_accessed: Optional[float] = None


@dataclass
class Relationship:
    """An edge in the knowledge graph"""
    id: str
    source_id: str
    target_id: str
    relation_type: RelationType
    properties: Dict[str, Any] = field(default_factory=dict)
    weight: float = 1.0  # Relationship strength
    created_at: float = field(default_factory=time.time)


@dataclass
class GraphQuery:
    """A query for traversing the knowledge graph"""
    start_entity: Optional[str] = None
    entity_type: Optional[EntityType] = None
    relation_types: List[RelationType] = field(default_factory=list)
    max_depth: int = 2
    limit: int = 50
    semantic_query: Optional[str] = None
    min_weight: float = 0.0


@dataclass
class GraphResult:
    """Result from a graph query"""
    entities: List[Entity]
    relationships: List[Relationship]
    paths: List[List[str]]  # Paths as lists of entity IDs
    query_time_ms: float


class KnowledgeGraph:
    """
    Graph-based memory system for ABLE.

    Stores information as interconnected entities with typed relationships.
    Supports:
    - Entity CRUD operations
    - Relationship management
    - Graph traversal queries
    - Semantic similarity search
    - Memory decay and consolidation

    Usage:
        graph = KnowledgeGraph(db_path="~/.able/memory/knowledge.db")
        await graph.initialize()

        # Add entities
        entity = await graph.add_entity("john", "John Doe", EntityType.PERSON)

        # Add relationships
        await graph.add_relationship(entity.id, other_id, RelationType.KNOWS)

        # Query
        results = await graph.query(GraphQuery(
            start_entity=entity.id,
            relation_types=[RelationType.KNOWS],
            max_depth=2
        ))
    """

    def __init__(
        self,
        db_path: Path = None,
        embedding_func: callable = None,
    ):
        self.db_path = Path(db_path or "~/.able/memory/knowledge.db").expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._embedding_func = embedding_func
        self._initialized = False

    async def initialize(self):
        """Initialize the database"""
        if self._initialized:
            return

        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

        cursor = self._conn.cursor()

        # Entities table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS entities (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                properties TEXT,
                embedding BLOB,
                created_at REAL,
                updated_at REAL,
                access_count INTEGER DEFAULT 0,
                last_accessed REAL
            )
        """)

        # Relationships table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS relationships (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                properties TEXT,
                weight REAL DEFAULT 1.0,
                created_at REAL,
                FOREIGN KEY (source_id) REFERENCES entities(id),
                FOREIGN KEY (target_id) REFERENCES entities(id)
            )
        """)

        # Indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_rel_source ON relationships(source_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_rel_target ON relationships(target_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_rel_type ON relationships(relation_type)")

        # FTS for text search
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(
                id, name, properties,
                content='entities',
                content_rowid='rowid'
            )
        """)

        self._conn.commit()
        self._initialized = True
        logger.info(f"Knowledge graph initialized at {self.db_path}")

    def _generate_id(self, name: str, type_str: str) -> str:
        """Generate a unique ID for an entity"""
        data = f"{name}:{type_str}:{time.time()}"
        return hashlib.md5(data.encode()).hexdigest()[:12]

    async def add_entity(
        self,
        name: str,
        entity_type: EntityType,
        properties: Dict = None,
        entity_id: str = None,
    ) -> Entity:
        """Add a new entity to the graph"""
        if not self._initialized:
            await self.initialize()

        entity_id = entity_id or self._generate_id(name, entity_type.value)
        now = time.time()

        # Compute embedding if function available
        embedding = None
        embedding_blob = None
        if self._embedding_func:
            text = f"{name} {json.dumps(properties or {})}"
            embedding = self._embedding_func(text)
            import struct
            embedding_blob = struct.pack(f'{len(embedding)}f', *embedding)

        cursor = self._conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO entities
            (id, name, entity_type, properties, embedding, created_at, updated_at, access_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
        """, (
            entity_id,
            name,
            entity_type.value,
            json.dumps(properties or {}),
            embedding_blob,
            now,
            now,
        ))

        # Update FTS
        cursor.execute("""
            INSERT INTO entities_fts(id, name, properties)
            VALUES (?, ?, ?)
        """, (entity_id, name, json.dumps(properties or {})))

        self._conn.commit()

        return Entity(
            id=entity_id,
            name=name,
            entity_type=entity_type,
            properties=properties or {},
            embedding=embedding,
            created_at=now,
            updated_at=now,
        )

    async def get_entity(self, entity_id: str) -> Optional[Entity]:
        """Get an entity by ID"""
        if not self._initialized:
            await self.initialize()

        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT * FROM entities WHERE id = ?
        """, (entity_id,))

        row = cursor.fetchone()
        if not row:
            return None

        # Update access tracking
        cursor.execute("""
            UPDATE entities
            SET access_count = access_count + 1, last_accessed = ?
            WHERE id = ?
        """, (time.time(), entity_id))
        self._conn.commit()

        return self._row_to_entity(row)

    def _row_to_entity(self, row) -> Entity:
        """Convert database row to Entity"""
        embedding = None
        if row['embedding']:
            import struct
            count = len(row['embedding']) // 4
            embedding = list(struct.unpack(f'{count}f', row['embedding']))

        return Entity(
            id=row['id'],
            name=row['name'],
            entity_type=EntityType(row['entity_type']),
            properties=json.loads(row['properties'] or '{}'),
            embedding=embedding,
            created_at=row['created_at'],
            updated_at=row['updated_at'],
            access_count=row['access_count'],
            last_accessed=row['last_accessed'],
        )

    async def add_relationship(
        self,
        source_id: str,
        target_id: str,
        relation_type: RelationType,
        properties: Dict = None,
        weight: float = 1.0,
        bidirectional: bool = False,
    ) -> Relationship:
        """Add a relationship between entities"""
        if not self._initialized:
            await self.initialize()

        rel_id = self._generate_id(f"{source_id}:{target_id}", relation_type.value)
        now = time.time()

        cursor = self._conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO relationships
            (id, source_id, target_id, relation_type, properties, weight, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            rel_id,
            source_id,
            target_id,
            relation_type.value,
            json.dumps(properties or {}),
            weight,
            now,
        ))

        # Add reverse relationship if bidirectional
        if bidirectional:
            rev_id = self._generate_id(f"{target_id}:{source_id}", relation_type.value)
            cursor.execute("""
                INSERT OR REPLACE INTO relationships
                (id, source_id, target_id, relation_type, properties, weight, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (rev_id, target_id, source_id, relation_type.value,
                  json.dumps(properties or {}), weight, now))

        self._conn.commit()

        return Relationship(
            id=rel_id,
            source_id=source_id,
            target_id=target_id,
            relation_type=relation_type,
            properties=properties or {},
            weight=weight,
            created_at=now,
        )

    async def get_relationships(
        self,
        entity_id: str,
        direction: str = "outgoing",  # outgoing, incoming, both
        relation_types: List[RelationType] = None,
    ) -> List[Relationship]:
        """Get relationships for an entity"""
        if not self._initialized:
            await self.initialize()

        cursor = self._conn.cursor()
        relationships = []

        if direction in ("outgoing", "both"):
            query = "SELECT * FROM relationships WHERE source_id = ?"
            params = [entity_id]

            if relation_types:
                placeholders = ",".join("?" * len(relation_types))
                query += f" AND relation_type IN ({placeholders})"
                params.extend([rt.value for rt in relation_types])

            cursor.execute(query, params)
            for row in cursor.fetchall():
                relationships.append(Relationship(
                    id=row['id'],
                    source_id=row['source_id'],
                    target_id=row['target_id'],
                    relation_type=RelationType(row['relation_type']),
                    properties=json.loads(row['properties'] or '{}'),
                    weight=row['weight'],
                    created_at=row['created_at'],
                ))

        if direction in ("incoming", "both"):
            query = "SELECT * FROM relationships WHERE target_id = ?"
            params = [entity_id]

            if relation_types:
                placeholders = ",".join("?" * len(relation_types))
                query += f" AND relation_type IN ({placeholders})"
                params.extend([rt.value for rt in relation_types])

            cursor.execute(query, params)
            for row in cursor.fetchall():
                relationships.append(Relationship(
                    id=row['id'],
                    source_id=row['source_id'],
                    target_id=row['target_id'],
                    relation_type=RelationType(row['relation_type']),
                    properties=json.loads(row['properties'] or '{}'),
                    weight=row['weight'],
                    created_at=row['created_at'],
                ))

        return relationships

    async def traverse(
        self,
        start_id: str,
        max_depth: int = 2,
        relation_types: List[RelationType] = None,
        min_weight: float = 0.0,
    ) -> Tuple[List[Entity], List[Relationship], List[List[str]]]:
        """
        Traverse the graph starting from an entity.

        Returns all reachable entities within max_depth,
        the relationships traversed, and the paths taken.
        """
        if not self._initialized:
            await self.initialize()

        visited: Set[str] = set()
        entities: Dict[str, Entity] = {}
        relationships: List[Relationship] = []
        paths: List[List[str]] = []

        # BFS traversal
        queue = [(start_id, 0, [start_id])]

        while queue:
            current_id, depth, path = queue.pop(0)

            if current_id in visited:
                continue
            visited.add(current_id)

            # Get entity
            entity = await self.get_entity(current_id)
            if entity:
                entities[current_id] = entity

            if depth > 0:
                paths.append(path)

            if depth >= max_depth:
                continue

            # Get outgoing relationships
            rels = await self.get_relationships(
                current_id,
                direction="outgoing",
                relation_types=relation_types
            )

            for rel in rels:
                if rel.weight < min_weight:
                    continue
                if rel.target_id not in visited:
                    relationships.append(rel)
                    queue.append((rel.target_id, depth + 1, path + [rel.target_id]))

        return list(entities.values()), relationships, paths

    async def query(self, query: GraphQuery) -> GraphResult:
        """Execute a graph query"""
        start_time = time.time()

        if query.start_entity:
            # Traversal from start entity
            entities, relationships, paths = await self.traverse(
                query.start_entity,
                max_depth=query.max_depth,
                relation_types=query.relation_types if query.relation_types else None,
                min_weight=query.min_weight,
            )
        else:
            # Search by type or semantic query
            entities = await self.search_entities(
                query.semantic_query,
                entity_type=query.entity_type,
                limit=query.limit,
            )
            relationships = []
            paths = []

        elapsed_ms = (time.time() - start_time) * 1000

        return GraphResult(
            entities=entities[:query.limit],
            relationships=relationships,
            paths=paths,
            query_time_ms=elapsed_ms,
        )

    async def search_entities(
        self,
        query: str = None,
        entity_type: EntityType = None,
        limit: int = 50,
    ) -> List[Entity]:
        """Search entities by text or type"""
        if not self._initialized:
            await self.initialize()

        cursor = self._conn.cursor()

        if query:
            # FTS search
            cursor.execute("""
                SELECT e.* FROM entities e
                JOIN entities_fts fts ON e.id = fts.id
                WHERE entities_fts MATCH ?
                LIMIT ?
            """, (query, limit))
        elif entity_type:
            cursor.execute("""
                SELECT * FROM entities WHERE entity_type = ? LIMIT ?
            """, (entity_type.value, limit))
        else:
            cursor.execute("SELECT * FROM entities LIMIT ?", (limit,))

        return [self._row_to_entity(row) for row in cursor.fetchall()]

    async def get_stats(self) -> Dict[str, Any]:
        """Get graph statistics"""
        if not self._initialized:
            await self.initialize()

        cursor = self._conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM entities")
        entity_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM relationships")
        rel_count = cursor.fetchone()[0]

        cursor.execute("""
            SELECT entity_type, COUNT(*) FROM entities GROUP BY entity_type
        """)
        by_type = {row[0]: row[1] for row in cursor.fetchall()}

        cursor.execute("""
            SELECT relation_type, COUNT(*) FROM relationships GROUP BY relation_type
        """)
        by_relation = {row[0]: row[1] for row in cursor.fetchall()}

        return {
            "total_entities": entity_count,
            "total_relationships": rel_count,
            "entities_by_type": by_type,
            "relationships_by_type": by_relation,
        }

    async def decay_memories(self, decay_factor: float = 0.95, min_access_days: int = 30):
        """
        Apply memory decay to rarely accessed entities.

        Reduces weight of relationships involving rarely accessed entities.
        """
        if not self._initialized:
            await self.initialize()

        cutoff = time.time() - (min_access_days * 86400)
        cursor = self._conn.cursor()

        # Find entities not accessed recently
        cursor.execute("""
            SELECT id FROM entities
            WHERE last_accessed IS NULL OR last_accessed < ?
        """, (cutoff,))

        stale_ids = [row[0] for row in cursor.fetchall()]

        # Decay relationship weights
        for entity_id in stale_ids:
            cursor.execute("""
                UPDATE relationships
                SET weight = weight * ?
                WHERE source_id = ? OR target_id = ?
            """, (decay_factor, entity_id, entity_id))

        self._conn.commit()
        logger.info(f"Applied decay to {len(stale_ids)} stale entities")

        return len(stale_ids)

    async def close(self):
        """Close database connection"""
        if self._conn:
            self._conn.close()
            self._conn = None
        self._initialized = False
