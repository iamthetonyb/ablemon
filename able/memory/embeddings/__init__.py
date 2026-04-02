"""
ABLE v2 Embeddings Module
Vector storage for semantic memory search.

Provides:
- VectorStore: Basic in-memory vector storage (fallback)
- SqliteVecStore: sqlite-vec indexed persistent storage (recommended)
"""

from .vector_store import VectorStore
from .sqlite_vec_store import SqliteVecStore

__all__ = ['VectorStore', 'SqliteVecStore']
