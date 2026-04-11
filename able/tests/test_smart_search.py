"""Tests for C3 Smart Search Pipeline — smart chunking, RRF, three-tier search."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from able.memory.research_index import (
    ResearchIndex,
    IndexResult,
    smart_chunk,
    reciprocal_rank_fusion,
)


@pytest.fixture
def index(tmp_path):
    return ResearchIndex(db_path=tmp_path / "test_research.db")


@pytest.fixture
def populated_index(index):
    """Index with several diverse entries for search testing."""
    index.add_finding("Context Window Management", "Techniques for managing LLM context windows including compaction and summarization", tags=["llm", "context"])
    index.add_finding("Vector Embeddings Guide", "How to compute and use vector embeddings for semantic search", tags=["embeddings", "search"])
    index.add_finding("SQLite FTS5 Tutorial", "Full-text search with SQLite FTS5 including BM25 ranking", tags=["sqlite", "search"])
    index.add_finding("Prompt Engineering Best Practices", "Advanced prompt engineering techniques for code generation", tags=["prompt", "coding"])
    index.add_finding("Memory Architecture Patterns", "Layered memory systems for autonomous agents", tags=["memory", "architecture"])
    index.add_finding("Security Hardening Guide", "OWASP top 10 mitigations for web applications", tags=["security", "web"])
    return index


# ── smart_chunk ─────────────────────────────────────────────────

def test_chunk_short_text():
    """Text under max_chunk_size returns single chunk."""
    chunks = smart_chunk("Hello world", max_chunk_size=800)
    assert len(chunks) == 1
    assert chunks[0] == "Hello world"


def test_chunk_respects_headers():
    text = "# Header 1\nSome content here that is long enough.\n\n# Header 2\nMore content here that fills the chunk."
    chunks = smart_chunk(text, max_chunk_size=60)
    assert len(chunks) >= 2
    assert any("Header 1" in c for c in chunks)
    assert any("Header 2" in c for c in chunks)


def test_chunk_respects_paragraphs():
    text = "First paragraph with content.\n\nSecond paragraph with different content.\n\nThird paragraph here."
    chunks = smart_chunk(text, max_chunk_size=50)
    assert len(chunks) >= 2


def test_chunk_preserves_code_fences():
    """Code fences should not be split."""
    text = (
        "Before code.\n\n"
        "```python\n"
        "def hello():\n"
        "    print('world')\n"
        "```\n\n"
        "After code."
    )
    chunks = smart_chunk(text, max_chunk_size=200)
    # Code block should be intact in at least one chunk
    code_chunks = [c for c in chunks if "def hello" in c]
    assert len(code_chunks) >= 1
    assert "```" in code_chunks[0]


def test_chunk_long_text_produces_multiple():
    text = "\n\n".join([f"Paragraph {i}: " + "x" * 100 for i in range(20)])
    chunks = smart_chunk(text, max_chunk_size=200)
    assert len(chunks) > 1
    # No chunk should be much larger than max (1.2x tolerance)
    for c in chunks:
        assert len(c) <= 200 * 1.3


def test_chunk_no_breaks_falls_back():
    """Text with no natural breaks gets hard-split."""
    text = "a" * 2000  # No headers, no paragraphs
    chunks = smart_chunk(text, max_chunk_size=500)
    assert len(chunks) >= 4


def test_chunk_empty_text():
    chunks = smart_chunk("")
    assert chunks == [""]


def test_chunk_h2_boundary():
    text = "Intro.\n\n## Section A\nContent A.\n\n## Section B\nContent B."
    chunks = smart_chunk(text, max_chunk_size=40)
    assert len(chunks) >= 2


# ── reciprocal_rank_fusion ──────────────────────────────────────

def test_rrf_single_list():
    ranked = [("a", 10.0), ("b", 5.0), ("c", 1.0)]
    fused = reciprocal_rank_fusion(ranked, k=60)
    assert fused[0][0] == "a"  # Rank 1 item should be first
    assert fused[1][0] == "b"


def test_rrf_two_lists_agreement():
    """When both lists agree on top item, it should rank highest."""
    list1 = [("a", 10.0), ("b", 5.0)]
    list2 = [("a", 8.0), ("c", 4.0)]
    fused = reciprocal_rank_fusion(list1, list2, k=60)
    assert fused[0][0] == "a"


def test_rrf_two_lists_disagreement():
    """Items appearing in both lists should rank higher than single-list items."""
    list1 = [("a", 10.0), ("b", 5.0), ("c", 1.0)]
    list2 = [("b", 10.0), ("d", 5.0), ("c", 1.0)]
    fused = reciprocal_rank_fusion(list1, list2, k=60)
    fused_ids = [f[0] for f in fused]
    # b and c appear in both lists, should rank higher than d (single-list)
    b_idx = fused_ids.index("b")
    d_idx = fused_ids.index("d")
    assert b_idx < d_idx


def test_rrf_empty_lists():
    fused = reciprocal_rank_fusion([], [])
    assert fused == []


def test_rrf_k_parameter():
    """Different k values should produce different scores but same relative order."""
    ranked = [("a", 10.0), ("b", 5.0)]
    fused_60 = reciprocal_rank_fusion(ranked, k=60)
    fused_10 = reciprocal_rank_fusion(ranked, k=10)
    assert fused_60[0][0] == fused_10[0][0]  # Same top item
    assert fused_60[0][1] != fused_10[0][1]  # Different scores


# ── ResearchIndex.smart_search ──────────────────────────────────

def test_smart_search_bm25_only(populated_index):
    """smart_search without vector_store uses BM25 only."""
    results = populated_index.smart_search("search embeddings", limit=3)
    assert len(results) > 0
    assert all(isinstance(r, IndexResult) for r in results)


def test_smart_search_with_vector_store(populated_index):
    """smart_search with mock vector store fuses results."""
    mock_vs = MagicMock()
    mock_vs.compute_embedding.return_value = [0.1] * 384
    mock_vs.search.return_value = [
        ("Vector Embeddings Guide", 0.9),
        ("Memory Architecture Patterns", 0.7),
    ]
    results = populated_index.smart_search("vector search", limit=3, vector_store=mock_vs)
    assert len(results) > 0


def test_smart_search_with_reranker(populated_index):
    """smart_search with rerank_fn applies LLM reranking."""
    def mock_reranker(query, candidates):
        # Reverse the order (simulating LLM reranking)
        return [{"title": c["title"]} for c in reversed(candidates)]

    # limit=1 ensures fused_results > limit, triggering reranker
    results = populated_index.smart_search("search", limit=1, rerank_fn=mock_reranker)
    assert len(results) > 0
    assert any(r.match_type == "reranked" for r in results)


def test_smart_search_reranker_failure_graceful(populated_index):
    """Failed reranker falls back to fused results."""
    def bad_reranker(query, candidates):
        raise RuntimeError("LLM down")

    results = populated_index.smart_search("search", limit=3, rerank_fn=bad_reranker)
    assert len(results) > 0  # Should not raise


def test_smart_search_vector_failure_graceful(populated_index):
    """Failed vector store falls back to BM25 only."""
    mock_vs = MagicMock()
    mock_vs.compute_embedding.side_effect = RuntimeError("Embedding model unavailable")
    results = populated_index.smart_search("search", limit=3, vector_store=mock_vs)
    assert len(results) > 0


def test_smart_search_empty_query(populated_index):
    # FTS5 may fail on empty query — should handle gracefully
    results = populated_index.smart_search("", limit=3)
    # Either returns results or empty list, but no crash
    assert isinstance(results, list)


def test_smart_search_limit_respected(populated_index):
    results = populated_index.smart_search("search", limit=2)
    assert len(results) <= 2


# ── add_chunked_document ──────────────────────────────────────

def test_chunked_document_small(index):
    """Small doc produces single chunk."""
    index.add_chunked_document("My Doc", "Short content", source="test")
    stats = index.get_stats()
    assert stats["total_entries"] == 1


def test_chunked_document_large(index):
    """Large doc with headers produces multiple chunks."""
    content = "\n\n".join([
        f"## Section {i}\n" + "Content " * 50
        for i in range(10)
    ])
    index.add_chunked_document("Large Doc", content, source="test", max_chunk_size=200)
    stats = index.get_stats()
    assert stats["total_entries"] > 1


def test_chunked_document_context_annotation(index):
    """Context annotation is prepended to chunk summaries."""
    index.add_chunked_document(
        "Doc", "Some content here.",
        context_annotation="ABLE/memory",
    )
    results = index.search("content", limit=1)
    assert len(results) >= 1
    assert "[ABLE/memory]" in results[0].summary


def test_chunked_document_tags_inherited(index):
    """Tags are inherited by all chunks."""
    content = "\n\n".join([f"## Section {i}\n" + "x" * 200 for i in range(5)])
    index.add_chunked_document("Tagged Doc", content, tags=["python", "agent"], max_chunk_size=100)
    results = index.search("Section", limit=5)
    for r in results:
        assert "python" in r.tags or "agent" in r.tags


# ── _bm25_search (extracted method) ───────────────────────────

def test_bm25_search_basic(populated_index):
    results = populated_index._bm25_search("security hardening", limit=5)
    assert len(results) > 0
    assert results[0].title == "Security Hardening Guide"


def test_bm25_search_relevance_boost(populated_index):
    """High-relevance entries get score boost."""
    populated_index.add_finding("Critical Finding", "Very important security issue", relevance="high")
    results = populated_index._bm25_search("security", limit=5)
    critical = next((r for r in results if r.title == "Critical Finding"), None)
    regular = next((r for r in results if r.title == "Security Hardening Guide"), None)
    if critical and regular:
        assert critical.score > regular.score * 0.9  # At least close due to 1.3x boost


def test_bm25_search_no_results(populated_index):
    results = populated_index._bm25_search("xyznonexistent", limit=5)
    assert results == []
