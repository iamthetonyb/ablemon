"""Tests for F2 — Auth Aliases + F11 — Credential Rotation.

Covers: credential pool, least-used selection, exhaustion cooldown,
auth alias registry, pooled aliases, simple aliases, stats.
"""

import os
import time
import pytest
from unittest.mock import patch

from able.core.routing.auth_aliases import (
    AuthAliasRegistry,
    Credential,
    CredentialPool,
    PoolStats,
)


# ── Credential ──────────────────────────────────────────────────

class TestCredential:

    def test_available_with_key(self):
        with patch.dict(os.environ, {"TEST_KEY": "sk-123"}):
            c = Credential(id="c1", env_var="TEST_KEY")
            assert c.is_available
            assert c.api_key == "sk-123"

    def test_unavailable_without_key(self):
        env = os.environ.copy()
        env.pop("MISSING_KEY_XYZ", None)
        with patch.dict(os.environ, env, clear=True):
            c = Credential(id="c1", env_var="MISSING_KEY_XYZ")
            assert not c.is_available

    def test_exhausted_not_available(self):
        with patch.dict(os.environ, {"TEST_KEY": "sk-123"}):
            c = Credential(id="c1", env_var="TEST_KEY", cooldown_s=3600)
            c.exhausted_at = time.time()
            assert not c.is_available

    def test_cooldown_expired_available(self):
        with patch.dict(os.environ, {"TEST_KEY": "sk-123"}):
            c = Credential(id="c1", env_var="TEST_KEY", cooldown_s=0.01)
            c.exhausted_at = time.time() - 1  # Well past cooldown
            assert c.is_available

    def test_cooldown_remaining(self):
        c = Credential(id="c1", env_var="X", cooldown_s=3600)
        c.exhausted_at = time.time()
        assert c.cooldown_remaining > 3500

    def test_no_cooldown(self):
        c = Credential(id="c1", env_var="X")
        assert c.cooldown_remaining == 0.0


# ── CredentialPool ──────────────────────────────────────────────

class TestCredentialPool:

    def test_get_next_least_used(self):
        with patch.dict(os.environ, {"K1": "a", "K2": "b"}):
            pool = CredentialPool()
            pool.add("c1", "K1")
            pool.add("c2", "K2")
            pool.mark_used("c1")
            pool.mark_used("c1")
            cred = pool.get_next()
            assert cred.id == "c2"  # Least used

    def test_get_next_empty_pool(self):
        pool = CredentialPool()
        assert pool.get_next() is None

    def test_get_next_all_exhausted(self):
        with patch.dict(os.environ, {"K1": "a"}):
            pool = CredentialPool()
            pool.add("c1", "K1")
            pool.mark_exhausted("c1")
            assert pool.get_next() is None

    def test_mark_used_clears_exhaustion(self):
        with patch.dict(os.environ, {"K1": "a"}):
            pool = CredentialPool()
            pool.add("c1", "K1")
            pool.mark_exhausted("c1")
            assert pool.get_next() is None
            # Simulate cooldown expired + successful use
            pool._credentials["c1"].exhausted_at = 0
            pool.mark_used("c1")
            assert pool._credentials["c1"].exhausted_at == 0.0

    def test_stats(self):
        with patch.dict(os.environ, {"K1": "a", "K2": "b"}):
            pool = CredentialPool()
            pool.add("c1", "K1")
            pool.add("c2", "K2")
            pool.mark_used("c1")
            pool.mark_exhausted("c2")
            s = pool.stats()
            assert s.total == 2
            assert s.available == 1
            assert s.exhausted == 1
            assert s.total_uses == 1

    def test_reset(self):
        with patch.dict(os.environ, {"K1": "a"}):
            pool = CredentialPool()
            pool.add("c1", "K1")
            pool.mark_used("c1")
            pool.mark_exhausted("c1")
            pool.reset()
            s = pool.stats()
            assert s.available == 1
            assert s.total_uses == 0


# ── AuthAliasRegistry ──────────────────────────────────────────

class TestAuthAliasRegistry:

    def test_simple_alias(self):
        with patch.dict(os.environ, {"MY_KEY": "secret"}):
            reg = AuthAliasRegistry()
            reg.register("openai", env_var="MY_KEY")
            assert reg.resolve("openai") == "secret"

    def test_resolve_missing(self):
        reg = AuthAliasRegistry()
        assert reg.resolve("nonexistent") is None

    def test_pooled_alias(self):
        with patch.dict(os.environ, {"K1": "a", "K2": "b"}):
            reg = AuthAliasRegistry()
            reg.register("openai", env_vars=["K1", "K2"])
            key = reg.resolve("openai")
            assert key in ("a", "b")

    def test_single_env_vars_list(self):
        with patch.dict(os.environ, {"K1": "a"}):
            reg = AuthAliasRegistry()
            reg.register("openai", env_vars=["K1"])
            assert reg.resolve("openai") == "a"

    def test_list_aliases(self):
        reg = AuthAliasRegistry()
        reg.register("openai", env_var="K1")
        reg.register("anthropic", env_var="K2")
        aliases = reg.list_aliases()
        assert "openai" in aliases
        assert "anthropic" in aliases

    def test_get_pool(self):
        with patch.dict(os.environ, {"K1": "a", "K2": "b"}):
            reg = AuthAliasRegistry()
            reg.register("openai", env_vars=["K1", "K2"])
            pool = reg.get_pool("openai")
            assert pool is not None
            assert pool.stats().total == 2

    def test_get_pool_simple_alias(self):
        reg = AuthAliasRegistry()
        reg.register("openai", env_var="K1")
        assert reg.get_pool("openai") is None  # Not pooled
