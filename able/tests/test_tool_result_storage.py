"""Tests for able.core.gateway.tool_result_storage — 3-layer context overflow defense."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from able.core.gateway.tool_result_storage import (
    DEFAULT_TOKEN_THRESHOLD,
    TURN_BUDGET_CHARS,
    maybe_persist_tool_result,
    enforce_turn_budget,
)


@pytest.fixture(autouse=True)
def use_tmp_storage(tmp_path, monkeypatch):
    """Redirect tool result storage to a temp dir."""
    monkeypatch.setattr(
        "able.core.gateway.tool_result_storage._get_storage_dir",
        lambda: tmp_path,
    )
    return tmp_path


# ── Layer 2: maybe_persist_tool_result ────────────────────────────

def test_small_output_not_persisted():
    output = "Short result"
    result, persisted = maybe_persist_tool_result("web_search", "id-1", output)
    assert result == output
    assert not persisted


def test_large_output_persisted(use_tmp_storage):
    output = "x" * (DEFAULT_TOKEN_THRESHOLD * 4 + 100)
    result, persisted = maybe_persist_tool_result("web_search", "id-large", output)
    assert persisted
    assert "[Full output saved to" in result
    assert "tokens" in result
    # Verify file was created
    files = list(use_tmp_storage.glob("*.txt"))
    assert len(files) == 1


def test_persist_creates_summary(use_tmp_storage):
    content = "IMPORTANT FINDING: " + "data " * 5000
    result, persisted = maybe_persist_tool_result("web_search", "id-summary", content)
    assert persisted
    assert "IMPORTANT FINDING" in result  # Summary includes first 500 chars


def test_empty_output_not_persisted():
    result, persisted = maybe_persist_tool_result("web_search", "id-empty", "")
    assert result == ""
    assert not persisted


def test_none_output_not_persisted():
    result, persisted = maybe_persist_tool_result("web_search", "id-none", None)
    assert result is None
    assert not persisted


# ── read_file exemption (prevents infinite loops) ─────────────────

def test_read_file_never_persisted():
    output = "x" * (DEFAULT_TOKEN_THRESHOLD * 4 + 100)
    result, persisted = maybe_persist_tool_result("read_file", "id-rf", output)
    assert not persisted
    assert result == output  # Original output unchanged


def test_Read_tool_never_persisted():
    output = "x" * (DEFAULT_TOKEN_THRESHOLD * 4 + 100)
    result, persisted = maybe_persist_tool_result("Read", "id-Read", output)
    assert not persisted


# ── File path sanitization ────────────────────────────────────────

def test_tool_use_id_sanitized(use_tmp_storage):
    output = "x" * (DEFAULT_TOKEN_THRESHOLD * 4 + 100)
    result, persisted = maybe_persist_tool_result(
        "web_search", "../../evil/path", output,
    )
    assert persisted
    # No directory traversal in filename
    files = list(use_tmp_storage.glob("*.txt"))
    assert len(files) == 1
    assert ".." not in files[0].name


# ── Layer 3: enforce_turn_budget ──────────────────────────────────

def test_under_budget_unchanged():
    outputs = [
        {"tool_name": "t1", "tool_use_id": "a", "output": "short"},
        {"tool_name": "t2", "tool_use_id": "b", "output": "also short"},
    ]
    result = enforce_turn_budget(outputs, budget_chars=1_000_000)
    assert result == outputs  # Unchanged


def test_over_budget_spills_largest(use_tmp_storage):
    small = "x" * 100
    large = "y" * (DEFAULT_TOKEN_THRESHOLD * 4 + 100)
    outputs = [
        {"tool_name": "t1", "tool_use_id": "small-1", "output": small},
        {"tool_name": "t2", "tool_use_id": "large-1", "output": large},
    ]
    # Set budget so only the large one needs spilling
    result = enforce_turn_budget(outputs, budget_chars=200)
    # Small output unchanged
    assert result[0]["output"] == small
    # Large output replaced with pointer
    assert "[Full output saved to" in result[1]["output"]


def test_over_budget_skips_small_outputs():
    """Small outputs (< threshold) should never be spilled."""
    outputs = [
        {"tool_name": f"t{i}", "tool_use_id": f"id-{i}", "output": "tiny"}
        for i in range(100)
    ]
    result = enforce_turn_budget(outputs, budget_chars=10)
    # All outputs too small to spill — unchanged
    for r in result:
        assert r["output"] == "tiny"
