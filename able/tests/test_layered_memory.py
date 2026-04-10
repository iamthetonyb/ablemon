"""Tests for able.memory.layered_memory — MemPalace 4-layer memory stack."""

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from able.memory.layered_memory import (
    L0_MAX_CHARS,
    L1_MAX_CHARS,
    LayeredMemory,
    LayeredMemoryConfig,
    MemoryLayer,
)


# ── MemoryLayer dataclass ───────────────────────────────────────


def test_layer_defaults():
    layer = MemoryLayer(level=0)
    assert layer.level == 0
    assert layer.content == ""
    assert layer.token_estimate == 0
    assert not layer.is_loaded


def test_layer_is_loaded():
    layer = MemoryLayer(level=1, content="something")
    assert layer.is_loaded


def test_layer_whitespace_not_loaded():
    layer = MemoryLayer(level=1, content="   \n  ")
    assert not layer.is_loaded


# ── LayeredMemoryConfig ─────────────────────────────────────────


def test_config_defaults():
    cfg = LayeredMemoryConfig()
    assert cfg.l0_max_chars == L0_MAX_CHARS
    assert cfg.l1_max_chars == L1_MAX_CHARS
    assert cfg.l2_limit == 5
    assert cfg.l3_limit == 10


def test_config_custom_paths(tmp_path):
    cfg = LayeredMemoryConfig(
        identity_path=tmp_path / "id.yaml",
        objectives_path=tmp_path / "obj.yaml",
    )
    assert cfg.identity_path == tmp_path / "id.yaml"


# ── L0: Identity ───────────────────────────────────────────────


def test_l0_default_when_no_files():
    cfg = LayeredMemoryConfig(
        identity_path=Path("/nonexistent/identity.yaml"),
        objectives_path=Path("/nonexistent/objectives.yaml"),
    )
    mem = LayeredMemory(config=cfg)
    l0 = mem._load_l0()
    assert "ABLE" in l0.content
    assert l0.token_estimate > 0
    assert l0.is_loaded


def test_l0_loads_identity_yaml(tmp_path):
    identity_file = tmp_path / "identity.yaml"
    identity_file.write_text("name: TestBot\nrole: testing agent\nowner: TestUser\n")

    cfg = LayeredMemoryConfig(
        identity_path=identity_file,
        objectives_path=Path("/nonexistent"),
    )
    mem = LayeredMemory(config=cfg)
    l0 = mem._load_l0()
    assert "TestBot" in l0.content
    assert "testing agent" in l0.content
    assert "TestUser" in l0.content


def test_l0_loads_objectives(tmp_path):
    identity_file = tmp_path / "identity.yaml"
    identity_file.write_text("name: Bot\nrole: agent\n")
    objectives_file = tmp_path / "objectives.yaml"
    objectives_file.write_text("objectives:\n  - Ship v2\n  - Fix bugs\n")

    cfg = LayeredMemoryConfig(
        identity_path=identity_file,
        objectives_path=objectives_file,
    )
    mem = LayeredMemory(config=cfg)
    l0 = mem._load_l0()
    assert "Ship v2" in l0.content
    assert "Fix bugs" in l0.content


def test_l0_truncates_long_content(tmp_path):
    identity_file = tmp_path / "identity.yaml"
    identity_file.write_text(f"name: Bot\nrole: {'x' * 500}\n")

    cfg = LayeredMemoryConfig(
        identity_path=identity_file,
        objectives_path=Path("/nonexistent"),
        l0_max_chars=50,
    )
    mem = LayeredMemory(config=cfg)
    l0 = mem._load_l0()
    assert len(l0.content) <= 50
    assert l0.content.endswith("...")


def test_l0_handles_corrupt_yaml(tmp_path):
    identity_file = tmp_path / "identity.yaml"
    identity_file.write_text("{{{{invalid yaml!!!!")

    cfg = LayeredMemoryConfig(
        identity_path=identity_file,
        objectives_path=Path("/nonexistent"),
    )
    mem = LayeredMemory(config=cfg)
    l0 = mem._load_l0()
    # Should fall back to default
    assert "ABLE" in l0.content


# ── L1: Essential Story ─────────────────────────────────────────


def test_l1_from_learnings_file(tmp_path):
    learnings = tmp_path / "learnings.md"
    learnings.write_text(textwrap.dedent("""\
        # Learnings
        - Always check manifest before creating tools
        - Verify API response format before chaining
        - Never assume batch support
    """))

    cfg = LayeredMemoryConfig(
        identity_path=Path("/nonexistent"),
        objectives_path=Path("/nonexistent"),
        learnings_path=learnings,
    )
    mem = LayeredMemory(config=cfg)
    l1 = mem._load_l1()
    assert "manifest" in l1.content
    assert "API response" in l1.content
    assert l1.source_count >= 3


def test_l1_truncates_long_content(tmp_path):
    learnings = tmp_path / "learnings.md"
    lines = "\n".join(f"- Learning number {i} with lots of detail" for i in range(100))
    learnings.write_text(lines)

    cfg = LayeredMemoryConfig(
        identity_path=Path("/nonexistent"),
        objectives_path=Path("/nonexistent"),
        learnings_path=learnings,
        l1_max_chars=200,
    )
    mem = LayeredMemory(config=cfg)
    l1 = mem._load_l1()
    assert len(l1.content) <= 200


def test_l1_empty_when_no_sources():
    cfg = LayeredMemoryConfig(
        identity_path=Path("/nonexistent"),
        objectives_path=Path("/nonexistent"),
        learnings_path=Path("/nonexistent"),
    )
    mem = LayeredMemory(config=cfg)
    l1 = mem._load_l1()
    assert not l1.is_loaded
    assert l1.source_count == 0


def test_l1_deduplicates_hybrid_entries(tmp_path):
    """Entries from HybridMemory that match learnings file shouldn't duplicate."""
    learnings = tmp_path / "learnings.md"
    learnings.write_text("- Check manifest first\n")

    mock_hybrid = MagicMock()
    mock_entry = MagicMock()
    mock_entry.content = "Check manifest first"
    mock_hybrid.search.return_value = [mock_entry]

    cfg = LayeredMemoryConfig(
        identity_path=Path("/nonexistent"),
        objectives_path=Path("/nonexistent"),
        learnings_path=learnings,
    )
    mem = LayeredMemory(config=cfg, hybrid_memory=mock_hybrid)
    l1 = mem._load_l1()
    # "Check manifest first" should appear once, not twice
    assert l1.content.count("Check manifest first") == 1


# ── L2: Filtered Retrieval ──────────────────────────────────────


def test_l2_returns_empty_without_hybrid():
    mem = LayeredMemory()
    l2 = mem._query_l2("routing")
    assert not l2.is_loaded
    assert l2.source_count == 0


def test_l2_queries_hybrid():
    mock_hybrid = MagicMock()
    mock_entry = MagicMock()
    mock_entry.content = "Routing uses 5-tier complexity scoring"
    mock_entry.memory_type = MagicMock(value="learning")
    mock_hybrid.search.return_value = [mock_entry]

    mem = LayeredMemory(hybrid_memory=mock_hybrid)
    l2 = mem._query_l2("routing")
    assert l2.is_loaded
    assert "5-tier" in l2.content
    assert l2.source_count == 1
    mock_hybrid.search.assert_called_once()


def test_l2_handles_search_failure():
    mock_hybrid = MagicMock()
    mock_hybrid.search.side_effect = Exception("DB locked")

    mem = LayeredMemory(hybrid_memory=mock_hybrid)
    l2 = mem._query_l2("anything")
    assert not l2.is_loaded


# ── L3: Deep Semantic Search ────────────────────────────────────


def test_l3_returns_empty_without_hybrid():
    mem = LayeredMemory()
    l3 = mem._query_l3("distillation pipeline")
    assert not l3.is_loaded


def test_l3_includes_metadata():
    mock_hybrid = MagicMock()
    mock_entry = MagicMock()
    mock_entry.content = "DPO pairs from Claude Code sessions"
    mock_entry.memory_type = MagicMock(value="conversation")
    mock_entry.metadata = {"source": "cli", "quality": "high"}
    mock_hybrid.search.return_value = [mock_entry]

    mem = LayeredMemory(hybrid_memory=mock_hybrid)
    l3 = mem._query_l3("distillation")
    assert "DPO pairs" in l3.content
    assert "source=cli" in l3.content


def test_l3_uses_lower_threshold():
    mock_hybrid = MagicMock()
    mock_hybrid.search.return_value = []

    mem = LayeredMemory(hybrid_memory=mock_hybrid)
    mem._query_l3("deep search query")

    call_kwargs = mock_hybrid.search.call_args
    assert call_kwargs.kwargs["min_score"] == 0.1  # Lower than L2's 0.3


# ── wake_up() ───────────────────────────────────────────────────


def test_wake_up_returns_l0_l1(tmp_path):
    identity_file = tmp_path / "identity.yaml"
    identity_file.write_text("name: TestBot\nrole: helper\n")
    learnings = tmp_path / "learnings.md"
    learnings.write_text("- Always test first\n- Never assume\n")

    cfg = LayeredMemoryConfig(
        identity_path=identity_file,
        objectives_path=Path("/nonexistent"),
        learnings_path=learnings,
    )
    mem = LayeredMemory(config=cfg)
    context = mem.wake_up()
    assert "[Identity]" in context
    assert "TestBot" in context
    assert "[Context]" in context
    assert "Always test first" in context


def test_wake_up_with_defaults():
    cfg = LayeredMemoryConfig(
        identity_path=Path("/nonexistent"),
        objectives_path=Path("/nonexistent"),
        learnings_path=Path("/nonexistent"),
    )
    mem = LayeredMemory(config=cfg)
    context = mem.wake_up()
    assert "[Identity]" in context
    assert "ABLE" in context


# ── recall() ────────────────────────────────────────────────────


def test_recall_depth_1_returns_wakeup():
    cfg = LayeredMemoryConfig(
        identity_path=Path("/nonexistent"),
        objectives_path=Path("/nonexistent"),
        learnings_path=Path("/nonexistent"),
    )
    mem = LayeredMemory(config=cfg)
    result = mem.recall("anything", depth=1)
    assert "[Identity]" in result


def test_recall_depth_2_includes_l2():
    mock_hybrid = MagicMock()
    mock_entry = MagicMock()
    mock_entry.content = "Relevant L2 result"
    mock_entry.memory_type = MagicMock(value="learning")
    mock_hybrid.search.return_value = [mock_entry]

    mem = LayeredMemory(hybrid_memory=mock_hybrid)
    mem._load_l0()  # Pre-load L0
    result = mem.recall("test query", depth=2)
    assert "Recalled" in result
    assert "Relevant L2 result" in result


def test_recall_depth_3_includes_l2_and_l3():
    mock_hybrid = MagicMock()
    mock_entry = MagicMock()
    mock_entry.content = "Deep result"
    mock_entry.memory_type = MagicMock(value="conversation")
    mock_entry.metadata = {}
    mock_hybrid.search.return_value = [mock_entry]

    mem = LayeredMemory(hybrid_memory=mock_hybrid)
    mem._load_l0()
    result = mem.recall("deep query", depth=3)
    assert "Deep Search" in result


# ── get_stats() ─────────────────────────────────────────────────


def test_get_stats_initial():
    mem = LayeredMemory()
    stats = mem.get_stats()
    assert "layers" in stats
    assert len(stats["layers"]) == 4
    assert stats["total_tokens"] == 0
    assert not stats["hybrid_memory_connected"]


def test_get_stats_after_wakeup(tmp_path):
    identity_file = tmp_path / "identity.yaml"
    identity_file.write_text("name: Bot\nrole: agent\n")

    cfg = LayeredMemoryConfig(
        identity_path=identity_file,
        objectives_path=Path("/nonexistent"),
        learnings_path=Path("/nonexistent"),
    )
    mem = LayeredMemory(config=cfg)
    mem.wake_up()
    stats = mem.get_stats()
    assert stats["layers"][0]["loaded"]
    assert stats["layers"][0]["token_estimate"] > 0


def test_get_stats_with_hybrid():
    mem = LayeredMemory(hybrid_memory=MagicMock())
    stats = mem.get_stats()
    assert stats["hybrid_memory_connected"]


# ── get_layer() ─────────────────────────────────────────────────


def test_get_layer_returns_correct_level():
    mem = LayeredMemory()
    layer = mem.get_layer(2)
    assert layer.level == 2


def test_get_layer_unknown_returns_empty():
    mem = LayeredMemory()
    layer = mem.get_layer(99)
    assert layer.level == 99
    assert not layer.is_loaded
