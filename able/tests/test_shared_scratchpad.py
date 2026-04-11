"""Tests for SharedScratchpad — cross-agent knowledge cache."""
import json
import tempfile
import time
from pathlib import Path

import pytest

from able.core.session.shared_scratchpad import SharedScratchpad


@pytest.fixture
def scratchpad(tmp_path):
    """Fresh scratchpad with temp DB."""
    return SharedScratchpad(db_path=str(tmp_path / "test_scratchpad.db"))


class TestPutGet:
    def test_put_and_get_string(self, scratchpad):
        scratchpad.put("key1", "value1")
        assert scratchpad.get("key1") == "value1"

    def test_put_and_get_dict(self, scratchpad):
        data = {"foo": "bar", "count": 42}
        scratchpad.put("key2", data)
        result = scratchpad.get("key2")
        assert json.loads(result) == data

    def test_get_missing_returns_none(self, scratchpad):
        assert scratchpad.get("nonexistent") is None

    def test_namespace_isolation(self, scratchpad):
        scratchpad.put("key", "global_val", namespace="global")
        scratchpad.put("key", "session_val", namespace="session-1")
        assert scratchpad.get("key", namespace="global") == "global_val"
        assert scratchpad.get("key", namespace="session-1") == "session_val"

    def test_upsert_replaces(self, scratchpad):
        scratchpad.put("key", "v1")
        scratchpad.put("key", "v2")
        assert scratchpad.get("key") == "v2"


class TestTTL:
    def test_expired_returns_none(self, scratchpad):
        scratchpad.put("key", "value", ttl_seconds=0)
        # Immediately expired
        time.sleep(0.01)
        assert scratchpad.get("key") is None

    def test_not_expired_returns_value(self, scratchpad):
        scratchpad.put("key", "value", ttl_seconds=3600)
        assert scratchpad.get("key") == "value"


class TestGetAll:
    def test_returns_all_entries(self, scratchpad):
        scratchpad.put("a", "1")
        scratchpad.put("b", "2")
        scratchpad.put("c", "3")
        entries = scratchpad.get_all()
        keys = [e["key"] for e in entries]
        assert set(keys) == {"a", "b", "c"}

    def test_excludes_expired(self, scratchpad):
        scratchpad.put("fresh", "yes", ttl_seconds=3600)
        scratchpad.put("stale", "no", ttl_seconds=0)
        time.sleep(0.01)
        entries = scratchpad.get_all()
        keys = [e["key"] for e in entries]
        assert "fresh" in keys
        assert "stale" not in keys


class TestContextBlock:
    def test_empty_returns_empty(self, scratchpad):
        assert scratchpad.get_context_block() == ""

    def test_formats_entries(self, scratchpad):
        scratchpad.put("file:/etc/config", "contains DB settings", source_agent="agent-1")
        block = scratchpad.get_context_block()
        assert "[SCRATCHPAD" in block
        assert "file:/etc/config" in block
        assert "(agent-1)" in block
        assert "[/SCRATCHPAD]" in block

    def test_respects_max_chars(self, scratchpad):
        for i in range(50):
            scratchpad.put(f"key-{i}", "x" * 200)
        block = scratchpad.get_context_block(max_chars=500)
        # Should be under budget + headers
        assert len(block) < 800

    def test_truncates_long_values(self, scratchpad):
        scratchpad.put("long", "x" * 500)
        block = scratchpad.get_context_block()
        assert "..." in block


class TestConvenienceMethods:
    def test_put_file_summary(self, scratchpad):
        scratchpad.put_file_summary("/path/to/file.py", "has 3 classes")
        assert scratchpad.get("file:/path/to/file.py") == "has 3 classes"

    def test_put_decision(self, scratchpad):
        scratchpad.put_decision("use-e4b", "E4B chosen for free T4 fit")
        assert scratchpad.get("decision:use-e4b") == "E4B chosen for free T4 fit"


class TestListKeys:
    def test_lists_active_keys(self, scratchpad):
        scratchpad.put("a", "1")
        scratchpad.put("b", "2")
        keys = scratchpad.list_keys()
        assert set(keys) == {"a", "b"}


class TestCleanup:
    def test_cleanup_removes_expired(self, scratchpad):
        scratchpad.put("stale", "old", ttl_seconds=0)
        scratchpad.put("fresh", "new", ttl_seconds=3600)
        time.sleep(0.01)
        removed = scratchpad.cleanup()
        assert removed >= 1
        assert scratchpad.get("fresh") == "new"
        assert scratchpad.get("stale") is None

    def test_clear_namespace(self, scratchpad):
        scratchpad.put("a", "1", namespace="ns1")
        scratchpad.put("b", "2", namespace="ns2")
        scratchpad.clear(namespace="ns1")
        assert scratchpad.get("a", namespace="ns1") is None
        assert scratchpad.get("b", namespace="ns2") == "2"

    def test_clear_all(self, scratchpad):
        scratchpad.put("a", "1")
        scratchpad.put("b", "2", namespace="ns2")
        scratchpad.clear()
        assert scratchpad.get("a") is None
        assert scratchpad.get("b", namespace="ns2") is None


class TestStats:
    def test_stats_counts(self, scratchpad):
        scratchpad.put("a", "1", entry_type="finding")
        scratchpad.put("b", "2", entry_type="file_summary")
        stats = scratchpad.stats()
        assert stats["total"] == 2
        assert stats["active"] == 2
        assert stats["expired"] == 0
        assert stats["by_type"]["finding"] == 1
        assert stats["by_type"]["file_summary"] == 1


class TestLazyInit:
    def test_init_does_not_create_db(self, tmp_path):
        db_path = str(tmp_path / "lazy" / "test.db")
        sp = SharedScratchpad(db_path=db_path)
        # DB dir should NOT exist yet
        assert not Path(db_path).exists()

    def test_first_put_creates_db(self, tmp_path):
        db_path = str(tmp_path / "lazy" / "test.db")
        sp = SharedScratchpad(db_path=db_path)
        sp.put("key", "val")
        assert Path(db_path).exists()

    def test_get_on_failed_init_returns_none(self, tmp_path):
        # Point at an unwritable path
        sp = SharedScratchpad(db_path="/dev/null/impossible/test.db")
        assert sp.get("key") is None

    def test_get_all_on_failed_init_returns_empty(self, tmp_path):
        sp = SharedScratchpad(db_path="/dev/null/impossible/test.db")
        assert sp.get_all() == []
