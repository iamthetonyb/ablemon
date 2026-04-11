"""Tests for D16 — Profile-Scoped Memory.

Covers: profile scoping, key namespacing, directory isolation,
token lock isolation, profile listing, stats.
"""

import pytest
from pathlib import Path

from able.memory.profile_scoping import (
    DEFAULT_PROFILE,
    ProfileMemoryManager,
    ProfileScope,
    ProfileStats,
)


@pytest.fixture
def manager(tmp_path):
    return ProfileMemoryManager(base_dir=tmp_path)


# ── ProfileScope ────────────────────────────────────────────────

class TestProfileScope:

    def test_scoped_key(self):
        scope = ProfileScope(profile_id="user-123")
        assert scope.scoped_key("prefs") == "user-123:prefs"

    def test_scoped_key_default(self):
        scope = ProfileScope(profile_id=DEFAULT_PROFILE)
        assert scope.scoped_key("prefs") == "prefs"  # No prefix

    def test_unscoped_key(self):
        scope = ProfileScope(profile_id="user-123")
        assert scope.unscoped_key("user-123:prefs") == "prefs"

    def test_unscoped_key_no_prefix(self):
        scope = ProfileScope(profile_id="user-123")
        assert scope.unscoped_key("other:prefs") == "other:prefs"

    def test_is_own_key(self):
        scope = ProfileScope(profile_id="user-123")
        assert scope.is_own_key("user-123:prefs")
        assert not scope.is_own_key("user-456:prefs")

    def test_is_own_key_default(self):
        scope = ProfileScope(profile_id=DEFAULT_PROFILE)
        assert scope.is_own_key("prefs")
        assert not scope.is_own_key("user-123:prefs")


# ── ProfileMemoryManager ───────────────────────────────────────

class TestManager:

    def test_get_scope(self, manager):
        scope = manager.get_scope("user-1")
        assert scope.profile_id == "user-1"

    def test_get_scope_creates_dir(self, manager, tmp_path):
        manager.get_scope("user-1")
        assert (tmp_path / "profiles" / "user-1").exists()

    def test_default_profile_uses_base(self, manager, tmp_path):
        pdir = manager.profile_dir(DEFAULT_PROFILE)
        assert pdir == tmp_path

    def test_profile_dir_sanitized(self, manager, tmp_path):
        pdir = manager.profile_dir("user/../../etc")
        # Should sanitize slashes and dots
        assert ".." not in pdir.name
        assert "/" not in pdir.name

    def test_list_profiles_empty(self, manager):
        profiles = manager.list_profiles()
        assert DEFAULT_PROFILE in profiles

    def test_list_profiles_after_create(self, manager):
        manager.get_scope("alice")
        manager.get_scope("bob")
        profiles = manager.list_profiles()
        assert "alice" in profiles
        assert "bob" in profiles

    def test_same_scope_returned(self, manager):
        s1 = manager.get_scope("user-1")
        s2 = manager.get_scope("user-1")
        assert s1 is s2


# ── Token lock isolation ────────────────────────────────────────

class TestTokenLocks:

    def test_acquire_lock(self, manager):
        assert manager.acquire_token_lock("user-1", "bot-token-123")

    def test_same_user_reacquire(self, manager):
        manager.acquire_token_lock("user-1", "bot-token")
        assert manager.acquire_token_lock("user-1", "bot-token")

    def test_different_user_blocked(self, manager):
        manager.acquire_token_lock("user-1", "bot-token")
        assert not manager.acquire_token_lock("user-2", "bot-token")

    def test_release_allows_other(self, manager):
        manager.acquire_token_lock("user-1", "bot-token")
        manager.release_token_lock("user-1", "bot-token")
        assert manager.acquire_token_lock("user-2", "bot-token")

    def test_release_wrong_user_noop(self, manager):
        manager.acquire_token_lock("user-1", "bot-token")
        manager.release_token_lock("user-2", "bot-token")  # Wrong user
        assert not manager.acquire_token_lock("user-2", "bot-token")


# ── Stats ───────────────────────────────────────────────────────

class TestStats:

    def test_empty_profile_stats(self, manager):
        s = manager.stats("user-1")
        assert isinstance(s, ProfileStats)
        assert s.memory_count == 0

    def test_stats_with_files(self, manager, tmp_path):
        manager.get_scope("user-1")
        pdir = manager.profile_dir("user-1")
        (pdir / "test.txt").write_text("hello")
        s = manager.stats("user-1")
        assert s.memory_count >= 1
