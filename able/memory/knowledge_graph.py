"""
C6 — Knowledge Graph Integration.

Builds a relation graph from memory entries and Trilium notes.
Provides context-aware recall by weighing semantic similarity AND
graph proximity. Discovers cross-domain "tunnels" (nodes appearing
in multiple clusters).

Usage:
    kg = KnowledgeGraph()
    kg.add_relation("ABLE", "uses", "Claude Opus 4")
    kg.add_relation("Claude Opus 4", "is_a", "LLM")
    kg.add_relation("ABLE", "uses", "Ollama")

    related = kg.query("ABLE")
    tunnels = kg.find_tunnels()
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class Relation:
    """A directed edge in the knowledge graph."""
    subject: str
    predicate: str
    object: str
    weight: float = 1.0
    source: str = ""  # Where this relation came from
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class QueryResult:
    """Result from a graph query."""
    entity: str
    relations: List[Relation] = field(default_factory=list)
    connected_entities: List[str] = field(default_factory=list)
    depth: int = 0


@dataclass
class Tunnel:
    """A cross-domain connection (entity appearing in multiple clusters)."""
    entity: str
    clusters: List[Set[str]] = field(default_factory=list)
    bridge_score: float = 0.0  # How strongly it bridges clusters


@dataclass
class GraphStats:
    """Knowledge graph statistics."""
    nodes: int = 0
    edges: int = 0
    predicates: int = 0
    clusters: int = 0
    tunnels: int = 0


class KnowledgeGraph:
    """In-memory knowledge graph with BFS traversal and cluster detection.

    Stores entity-relation-entity triples as an adjacency list.
    Supports bidirectional traversal, context-aware recall, and
    cross-domain tunnel discovery.
    """

    def __init__(self):
        # Adjacency lists: entity → [(predicate, target, Relation)]
        self._outgoing: Dict[str, List[Relation]] = defaultdict(list)
        self._incoming: Dict[str, List[Relation]] = defaultdict(list)
        self._nodes: Set[str] = set()
        self._predicates: Set[str] = set()

    def add_relation(
        self,
        subject: str,
        predicate: str,
        obj: str,
        weight: float = 1.0,
        source: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Relation:
        """Add a relation (edge) to the graph.

        Args:
            subject: Source entity.
            predicate: Relationship type.
            obj: Target entity.
            weight: Edge weight (default 1.0).
            source: Origin of this relation.
            metadata: Optional extra data.

        Returns:
            The created Relation.
        """
        rel = Relation(
            subject=subject,
            predicate=predicate,
            object=obj,
            weight=weight,
            source=source,
            metadata=metadata or {},
        )
        self._outgoing[subject].append(rel)
        self._incoming[obj].append(rel)
        self._nodes.add(subject)
        self._nodes.add(obj)
        self._predicates.add(predicate)
        return rel

    def remove_entity(self, entity: str) -> int:
        """Remove an entity and all its relations.

        Returns the number of relations removed.
        """
        removed = 0
        # Remove outgoing
        if entity in self._outgoing:
            for rel in self._outgoing[entity]:
                self._incoming[rel.object] = [
                    r for r in self._incoming.get(rel.object, [])
                    if r.subject != entity
                ]
                removed += 1
            del self._outgoing[entity]

        # Remove incoming
        if entity in self._incoming:
            for rel in self._incoming[entity]:
                self._outgoing[rel.subject] = [
                    r for r in self._outgoing.get(rel.subject, [])
                    if r.object != entity
                ]
                removed += 1
            del self._incoming[entity]

        self._nodes.discard(entity)
        return removed

    def query(
        self,
        entity: str,
        max_depth: int = 2,
        max_results: int = 50,
    ) -> QueryResult:
        # Clamp max_depth to prevent unbounded BFS traversal
        max_depth = max(0, min(max_depth, 20))
        """Query the graph for an entity and its neighborhood.

        Uses BFS to find connected entities up to max_depth hops.
        """
        if entity not in self._nodes:
            return QueryResult(entity=entity)

        relations: List[Relation] = []
        connected: List[str] = []
        visited: Set[str] = {entity}
        queue: deque[Tuple[str, int]] = deque([(entity, 0)])

        while queue and len(connected) < max_results:
            current, depth = queue.popleft()

            # Collect outgoing relations
            for rel in self._outgoing.get(current, []):
                relations.append(rel)
                if rel.object not in visited and depth < max_depth:
                    visited.add(rel.object)
                    connected.append(rel.object)
                    queue.append((rel.object, depth + 1))

            # Collect incoming relations
            for rel in self._incoming.get(current, []):
                relations.append(rel)
                if rel.subject not in visited and depth < max_depth:
                    visited.add(rel.subject)
                    connected.append(rel.subject)
                    queue.append((rel.subject, depth + 1))

        return QueryResult(
            entity=entity,
            relations=relations,
            connected_entities=connected,
            depth=max_depth,
        )

    def find_by_predicate(
        self, predicate: str, max_results: int = 500,
    ) -> List[Relation]:
        """Find all relations with a given predicate.

        Args:
            predicate: The predicate to search for.
            max_results: Maximum results to return (default 500, capped at 5000).
        """
        max_results = max(1, min(max_results, 5000))
        results = []
        for rels in self._outgoing.values():
            for rel in rels:
                if rel.predicate == predicate:
                    results.append(rel)
                    if len(results) >= max_results:
                        return results
        return results

    def find_tunnels(self, min_cluster_size: int = 2) -> List[Tunnel]:
        """Find cross-domain tunnels.

        A tunnel is an entity that appears in multiple disconnected
        clusters, creating a bridge between otherwise separate domains.
        """
        clusters = self._find_clusters()
        if len(clusters) < 2:
            return []

        # Find entities present in multiple clusters
        entity_clusters: Dict[str, List[int]] = defaultdict(list)
        for i, cluster in enumerate(clusters):
            if len(cluster) < min_cluster_size:
                continue
            for entity in cluster:
                entity_clusters[entity].append(i)

        tunnels = []
        for entity, cluster_indices in entity_clusters.items():
            if len(cluster_indices) > 1:
                # Bridge score = number of clusters bridged
                bridge_score = len(cluster_indices) / len(clusters)
                tunnels.append(Tunnel(
                    entity=entity,
                    clusters=[clusters[i] for i in cluster_indices],
                    bridge_score=bridge_score,
                ))

        tunnels.sort(key=lambda t: t.bridge_score, reverse=True)
        return tunnels

    def context_aware_recall(
        self,
        query_entity: str,
        context_entities: Optional[List[str]] = None,
        max_results: int = 10,
    ) -> List[Relation]:
        """Recall relations weighted by graph proximity to context.

        Relations connected to both the query entity AND context entities
        are scored higher, enabling context-dependent recall.
        """
        context_entities = context_entities or []
        context_set = set(context_entities)

        # Get all relations for query entity (2-hop)
        result = self.query(query_entity, max_depth=2)

        # Score each relation by context proximity
        scored: List[Tuple[float, Relation]] = []
        for rel in result.relations:
            score = rel.weight
            # Boost if subject or object is in context
            if rel.subject in context_set or rel.object in context_set:
                score *= 2.0
            # Boost if connected to query entity directly
            if rel.subject == query_entity or rel.object == query_entity:
                score *= 1.5
            scored.append((score, rel))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [rel for _, rel in scored[:max_results]]

    def stats(self) -> GraphStats:
        """Get graph statistics."""
        edge_count = sum(len(rels) for rels in self._outgoing.values())
        clusters = self._find_clusters()
        tunnels = self.find_tunnels()
        return GraphStats(
            nodes=len(self._nodes),
            edges=edge_count,
            predicates=len(self._predicates),
            clusters=len(clusters),
            tunnels=len(tunnels),
        )

    def _find_clusters(self) -> List[Set[str]]:
        """Find connected components using BFS."""
        visited: Set[str] = set()
        clusters: List[Set[str]] = []

        for node in self._nodes:
            if node in visited:
                continue

            cluster: Set[str] = set()
            queue: deque[str] = deque([node])

            while queue:
                current = queue.popleft()
                if current in visited:
                    continue
                visited.add(current)
                cluster.add(current)

                # Follow outgoing
                for rel in self._outgoing.get(current, []):
                    if rel.object not in visited:
                        queue.append(rel.object)
                # Follow incoming
                for rel in self._incoming.get(current, []):
                    if rel.subject not in visited:
                        queue.append(rel.subject)

            if cluster:
                clusters.append(cluster)

        return clusters

    def to_dict(self) -> Dict[str, Any]:
        """Serialize graph to dict."""
        edges = []
        for rels in self._outgoing.values():
            for rel in rels:
                edges.append({
                    "subject": rel.subject,
                    "predicate": rel.predicate,
                    "object": rel.object,
                    "weight": rel.weight,
                    "source": rel.source,
                })
        return {
            "nodes": sorted(self._nodes),
            "edges": edges,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> KnowledgeGraph:
        """Deserialize graph from dict."""
        kg = cls()
        for edge in data.get("edges", []):
            kg.add_relation(
                subject=edge["subject"],
                predicate=edge["predicate"],
                obj=edge["object"],
                weight=edge.get("weight", 1.0),
                source=edge.get("source", ""),
            )
        return kg
