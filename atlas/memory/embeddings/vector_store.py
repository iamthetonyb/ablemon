"""
Vector Store for Semantic Search
Simple vector database with cosine similarity search.
Supports external embedding APIs (OpenAI, local models).
"""

import json
import hashlib
from pathlib import Path
from typing import List, Optional, Dict, Tuple, Any
from dataclasses import dataclass
import struct

@dataclass
class VectorEntry:
    """A vector entry with metadata"""
    id: str
    vector: List[float]
    metadata: Dict[str, Any]

class VectorStore:
    """
    Simple vector store with cosine similarity search.
    Stores vectors in a binary file for efficiency.
    """

    def __init__(
        self,
        storage_path: Path,
        embedding_dim: int = 384,  # Default for small models
        embedding_provider: str = "simple"  # "simple", "openai", "local"
    ):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

        self.embedding_dim = embedding_dim
        self.embedding_provider = embedding_provider

        # In-memory index
        self.vectors: Dict[str, VectorEntry] = {}
        self.index_path = self.storage_path.with_suffix('.index.json')

        self._load()

    def _load(self):
        """Load vectors from disk"""
        if self.index_path.exists():
            with open(self.index_path) as f:
                index_data = json.load(f)

            if self.storage_path.exists():
                with open(self.storage_path, 'rb') as f:
                    for entry_data in index_data.get('entries', []):
                        entry_id = entry_data['id']
                        offset = entry_data['offset']
                        metadata = entry_data.get('metadata', {})

                        f.seek(offset)
                        vector_bytes = f.read(self.embedding_dim * 4)  # 4 bytes per float
                        vector = list(struct.unpack(f'{self.embedding_dim}f', vector_bytes))

                        self.vectors[entry_id] = VectorEntry(
                            id=entry_id,
                            vector=vector,
                            metadata=metadata
                        )

    def _save(self):
        """Save vectors to disk"""
        index_data = {'entries': [], 'dim': self.embedding_dim}

        with open(self.storage_path, 'wb') as f:
            offset = 0
            for entry_id, entry in self.vectors.items():
                # Write vector
                vector_bytes = struct.pack(f'{self.embedding_dim}f', *entry.vector)
                f.write(vector_bytes)

                index_data['entries'].append({
                    'id': entry_id,
                    'offset': offset,
                    'metadata': entry.metadata
                })
                offset += len(vector_bytes)

        with open(self.index_path, 'w') as f:
            json.dump(index_data, f)

    def compute_embedding(self, text: str) -> Optional[List[float]]:
        """
        Compute embedding for text.
        Uses simple hash-based embedding by default (fast but not semantic).
        Can be upgraded to use OpenAI or local models.
        """
        if self.embedding_provider == "simple":
            return self._simple_embedding(text)
        elif self.embedding_provider == "openai":
            return self._openai_embedding(text)
        elif self.embedding_provider == "local":
            return self._local_embedding(text)
        return None

    def _simple_embedding(self, text: str) -> List[float]:
        """
        Simple hash-based embedding (fast, but not semantic).
        Useful for exact/fuzzy matching, not true semantic search.
        """
        # Create a deterministic pseudo-embedding from text hash
        text_lower = text.lower()

        # Use multiple hash functions for different "dimensions"
        embedding = []
        for i in range(self.embedding_dim):
            hash_input = f"{text_lower}:{i}"
            hash_value = int(hashlib.md5(hash_input.encode()).hexdigest()[:8], 16)
            # Normalize to [-1, 1]
            normalized = (hash_value / (2**31)) - 1
            embedding.append(normalized)

        # Normalize to unit vector
        magnitude = sum(x**2 for x in embedding) ** 0.5
        if magnitude > 0:
            embedding = [x / magnitude for x in embedding]

        return embedding

    def _openai_embedding(self, text: str) -> Optional[List[float]]:
        """Get embedding from OpenAI API"""
        try:
            from openai import OpenAI
            import os

            # Try to get API key
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                secrets_path = Path.home() / ".atlas" / ".secrets" / "OPENAI_API_KEY"
                if secrets_path.exists():
                    api_key = secrets_path.read_text().strip()

            if not api_key:
                return self._simple_embedding(text)

            client = OpenAI(api_key=api_key)
            response = client.embeddings.create(
                model="text-embedding-3-small",
                input=text[:8000]  # Limit input length
            )
            return response.data[0].embedding

        except Exception as e:
            print(f"OpenAI embedding error: {e}")
            return self._simple_embedding(text)

    _st_model = None  # Class-level cache for SentenceTransformer

    def _local_embedding(self, text: str) -> Optional[List[float]]:
        """Get embedding from local model (e.g., sentence-transformers)"""
        try:
            if VectorStore._st_model is None:
                from sentence_transformers import SentenceTransformer
                VectorStore._st_model = SentenceTransformer('all-MiniLM-L6-v2')
            embedding = VectorStore._st_model.encode(text, convert_to_numpy=True).tolist()
            return embedding
        except ImportError:
            return self._simple_embedding(text)

    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Compute cosine similarity between two vectors"""
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        magnitude1 = sum(a**2 for a in vec1) ** 0.5
        magnitude2 = sum(b**2 for b in vec2) ** 0.5

        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0

        return dot_product / (magnitude1 * magnitude2)

    def add(
        self,
        entry_id: str,
        vector: List[float],
        metadata: Dict[str, Any] = None
    ):
        """Add a vector to the store"""
        # Ensure correct dimension
        if len(vector) != self.embedding_dim:
            # Pad or truncate
            if len(vector) < self.embedding_dim:
                vector = vector + [0.0] * (self.embedding_dim - len(vector))
            else:
                vector = vector[:self.embedding_dim]

        self.vectors[entry_id] = VectorEntry(
            id=entry_id,
            vector=vector,
            metadata=metadata or {}
        )
        self._save()

    def search(
        self,
        query_vector: List[float],
        limit: int = 10,
        filter_metadata: Dict[str, Any] = None
    ) -> List[Tuple[str, float]]:
        """Search for similar vectors"""
        results = []

        for entry_id, entry in self.vectors.items():
            # Apply metadata filter
            if filter_metadata:
                match = True
                for key, value in filter_metadata.items():
                    if value is not None and entry.metadata.get(key) != value:
                        match = False
                        break
                if not match:
                    continue

            similarity = self._cosine_similarity(query_vector, entry.vector)
            results.append((entry_id, similarity))

        # Sort by similarity (descending)
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]

    def delete(self, entry_id: str) -> bool:
        """Delete a vector"""
        if entry_id in self.vectors:
            del self.vectors[entry_id]
            self._save()
            return True
        return False

    def count(self) -> int:
        """Count vectors in store"""
        return len(self.vectors)

    def clear(self):
        """Clear all vectors"""
        self.vectors.clear()
        self._save()
