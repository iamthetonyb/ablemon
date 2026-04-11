"""Tests for memory freshness / staleness warnings (Claurst pattern)."""

import time
import pytest
from datetime import datetime, timezone
from able.memory.freshness import check_freshness, annotate_memory, FreshnessResult


class TestCheckFreshness:

    def test_recent_is_fresh(self):
        result = check_freshness(time.time() - 3600)  # 1 hour ago
        assert not result.is_stale
        assert result.caveat == ""

    def test_yesterday_is_fresh(self):
        result = check_freshness(time.time() - 86400)  # 24 hours
        assert not result.is_stale

    def test_3_days_has_warning(self):
        result = check_freshness(time.time() - 3 * 86400)
        assert not result.is_stale
        assert "3 days" in result.caveat

    def test_10_days_is_stale(self):
        result = check_freshness(time.time() - 10 * 86400)
        assert result.is_stale
        assert "STALE" in result.caveat
        assert "10 days" in result.caveat

    def test_60_days_is_very_stale(self):
        result = check_freshness(time.time() - 60 * 86400)
        assert result.is_stale
        assert "months" in result.caveat

    def test_120_days_is_archival(self):
        result = check_freshness(time.time() - 120 * 86400)
        assert result.is_stale
        assert "Archival" in result.caveat

    def test_iso_string_input(self):
        result = check_freshness("2020-01-01T00:00:00Z")
        assert result.is_stale
        assert result.age_days > 365

    def test_datetime_input(self):
        dt = datetime(2020, 1, 1, tzinfo=timezone.utc)
        result = check_freshness(dt)
        assert result.is_stale

    def test_bad_string_input(self):
        result = check_freshness("not-a-date")
        assert result.is_stale
        assert "unknown age" in result.caveat

    def test_future_timestamp(self):
        result = check_freshness(time.time() + 86400)
        assert not result.is_stale
        assert result.age_days == 0

    def test_custom_stale_threshold(self):
        ts = time.time() - 3 * 86400
        result_default = check_freshness(ts, stale_after_days=7)
        result_strict = check_freshness(ts, stale_after_days=2)
        assert not result_default.is_stale
        assert result_strict.is_stale


class TestAnnotateMemory:

    def test_fresh_memory_unchanged(self):
        content = "User prefers dark mode"
        result = annotate_memory(content, time.time())
        assert result == content

    def test_stale_memory_gets_caveat(self):
        content = "User prefers dark mode"
        result = annotate_memory(content, time.time() - 30 * 86400)
        assert result.startswith("[STALE")
        assert content in result

    def test_warning_memory(self):
        content = "API endpoint changed"
        result = annotate_memory(content, time.time() - 4 * 86400)
        assert "Verify" in result
        assert content in result
