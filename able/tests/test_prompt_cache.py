"""Tests for E3 — Gateway Prompt Caching.

Covers: session key generation, put/get, TTL expiration, prefix stability,
early message stability, turn tracking, invalidation, LRU eviction,
session cleanup, stats.
"""

import time
import pytest

from able.core.gateway.prompt_cache import (
    CacheStats,
    PromptCache,
    PromptCacheEntry,
    _fingerprint,
)


@pytest.fixture
def cache():
    return PromptCache(max_entries=5, max_age_s=10.0)


def _entry(prompt="You are ABLE.", chain=None):
    return PromptCacheEntry(system_prompt=prompt, provider_chain=chain)


# ── Key generation ─────────────────────────────────────────────

class TestSessionKey:

    def test_key_format(self):
        key = PromptCache.session_key("user-123", tier=4)
        assert key == "user-123:t4"

    def test_default_tier(self):
        key = PromptCache.session_key("s1")
        assert key == "s1:t0"


# ── Put / Get ──────────────────────────────────────────────────

class TestPutGet:

    def test_basic_put_get(self, cache):
        entry = _entry()
        cache.put("k1", entry)
        got = cache.get("k1")
        assert got is not None
        assert got.system_prompt == "You are ABLE."

    def test_miss(self, cache):
        assert cache.get("nonexistent") is None

    def test_hit_increments_count(self, cache):
        cache.put("k1", _entry())
        cache.get("k1")
        cache.get("k1")
        entry = cache.get("k1")
        assert entry.hit_count == 3

    def test_size(self, cache):
        cache.put("a", _entry())
        cache.put("b", _entry())
        assert cache.size == 2


# ── TTL expiration ─────────────────────────────────────────────

class TestTTL:

    def test_expired_entry(self):
        cache = PromptCache(max_age_s=0.01)  # 10ms TTL
        cache.put("k1", _entry())
        time.sleep(0.02)
        assert cache.get("k1") is None

    def test_cleanup_expired(self):
        cache = PromptCache(max_age_s=0.01)
        cache.put("a", _entry())
        cache.put("b", _entry())
        time.sleep(0.02)
        removed = cache.cleanup_expired()
        assert removed == 2
        assert cache.size == 0


# ── Prefix stability ──────────────────────────────────────────

class TestPrefixStability:

    def test_stable_prefix(self, cache):
        prompt = "You are ABLE."
        cache.put("k1", _entry(prompt))
        assert cache.is_prefix_stable("k1", prompt) is True

    def test_changed_prefix(self, cache):
        cache.put("k1", _entry("You are ABLE."))
        assert cache.is_prefix_stable("k1", "You are a different bot.") is False

    def test_prefix_missing_key(self, cache):
        assert cache.is_prefix_stable("ghost", "anything") is False

    def test_prefix_change_tracked_in_stats(self, cache):
        cache.put("k1", _entry("A"))
        cache.is_prefix_stable("k1", "B")
        assert cache.stats.prefix_changes == 1


# ── Early message stability ────────────────────────────────────

class TestEarlyMessages:

    def test_stable_early_messages(self, cache):
        msgs = [{"role": "user", "content": "hi"}]
        entry = PromptCacheEntry(system_prompt="sp", early_messages=msgs)
        cache.put("k1", entry)
        assert cache.is_early_messages_stable("k1", msgs) is True

    def test_changed_early_messages(self, cache):
        msgs = [{"role": "user", "content": "hi"}]
        entry = PromptCacheEntry(system_prompt="sp", early_messages=msgs)
        cache.put("k1", entry)
        assert cache.is_early_messages_stable(
            "k1", [{"role": "user", "content": "hello"}]
        ) is False


# ── Turn tracking ──────────────────────────────────────────────

class TestTurnTracking:

    def test_record_turn(self, cache):
        cache.put("k1", _entry())
        cache.record_turn("k1")
        cache.record_turn("k1")
        entry = cache.get("k1")
        assert entry.turn_count == 2

    def test_record_turn_missing(self, cache):
        # Should not raise
        cache.record_turn("ghost")


# ── Invalidation ──────────────────────────────────────────────

class TestInvalidation:

    def test_invalidate_key(self, cache):
        cache.put("k1", _entry())
        assert cache.invalidate("k1") is True
        assert cache.get("k1") is None

    def test_invalidate_nonexistent(self, cache):
        assert cache.invalidate("ghost") is False

    def test_invalidate_session(self, cache):
        cache.put("s1:t1", _entry())
        cache.put("s1:t4", _entry())
        cache.put("s2:t1", _entry())
        removed = cache.invalidate_session("s1")
        assert removed == 2
        assert cache.size == 1

    def test_invalidation_stats(self, cache):
        cache.put("k1", _entry())
        cache.invalidate("k1")
        assert cache.stats.invalidations == 1


# ── LRU eviction ──────────────────────────────────────────────

class TestEviction:

    def test_evict_on_capacity(self, cache):
        for i in range(6):
            cache.put(f"k{i}", _entry())
        assert cache.size == 5  # Max entries = 5

    def test_lru_evicted(self, cache):
        # Fill cache
        for i in range(5):
            cache.put(f"k{i}", _entry())
            time.sleep(0.001)
        # Touch k0 so it's recently used
        cache.get("k0")
        # Add one more — k1 should be evicted (oldest last_hit_at)
        cache.put("k5", _entry())
        assert cache.get("k1") is None
        assert cache.get("k0") is not None


# ── Stats ──────────────────────────────────────────────────────

class TestStats:

    def test_hit_rate(self, cache):
        cache.put("k1", _entry())
        cache.get("k1")  # hit
        cache.get("k2")  # miss
        assert cache.stats.hit_rate == 0.5

    def test_summary(self, cache):
        s = cache.stats.summary()
        assert "lookups=" in s
        assert "hit_rate=" in s

    def test_all_keys(self, cache):
        cache.put("a", _entry())
        cache.put("b", _entry())
        assert set(cache.all_keys()) == {"a", "b"}


# ── Fingerprint ────────────────────────────────────────────────

class TestFingerprint:

    def test_deterministic(self):
        assert _fingerprint("test") == _fingerprint("test")

    def test_different_inputs(self):
        assert _fingerprint("a") != _fingerprint("b")

    def test_length(self):
        assert len(_fingerprint("test")) == 16
