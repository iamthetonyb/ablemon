"""
ATLAS v2 Memory System
Hybrid memory with SQLite storage and vector embeddings for semantic search.
Bridges with v1 (~/.atlas/memory) for shared persistence.
"""

from pathlib import Path
from typing import Optional

# Default paths
ATLAS_V1_HOME = Path.home() / ".atlas"
ATLAS_V2_HOME = Path(__file__).parent.parent

def get_v1_memory_path() -> Optional[Path]:
    """Get v1 memory path if it exists"""
    v1_path = ATLAS_V1_HOME / "memory"
    return v1_path if v1_path.exists() else None

def get_v2_memory_path() -> Path:
    """Get v2 memory path"""
    return Path(__file__).parent

# Lazy imports to avoid circular dependencies
def __getattr__(name):
    if name == 'HybridMemory':
        from .hybrid_memory import HybridMemory
        return HybridMemory
    elif name == 'SQLiteStore':
        from .db.sqlite_store import SQLiteStore
        return SQLiteStore
    elif name == 'VectorStore':
        from .embeddings.vector_store import VectorStore
        return VectorStore
    elif name == 'TranscriptStore':
        from .transcripts.transcript_store import TranscriptStore
        return TranscriptStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    'HybridMemory',
    'SQLiteStore',
    'VectorStore',
    'TranscriptStore',
    'get_v1_memory_path',
    'get_v2_memory_path',
]
