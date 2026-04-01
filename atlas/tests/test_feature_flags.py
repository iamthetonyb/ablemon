#!/usr/bin/env python3
"""
Tests for the feature flag system and failure circuit breaker.
"""

import os
import sys
import tempfile
import time
from pathlib import Path

# Ensure atlas package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.feature_flags import FailureCircuitBreaker, FeatureFlagService


# ═══════════════════════════════════════════════════════════════
# FEATURE FLAG SERVICE TESTS
# ═══════════════════════════════════════════════════════════════

SAMPLE_FLAGS_YAML = """\
version: 1
flags:
  active_flag:
    enabled: true
    description: "An active flag"
  disabled_flag:
    enabled: false
    description: "A disabled flag"
  rollout_flag:
    enabled: true
    rollout_pct: 50
    description: "50% rollout"
  tenant_flag:
    enabled: true
    tenant_ids:
      - tenant_a
      - tenant_b
    description: "Tenant-specific flag"
  expired_flag:
    enabled: true
    expires_at: "2020-01-01T00:00:00"
    description: "Already expired"
  future_flag:
    enabled: true
    expires_at: "2099-12-31T23:59:59"
    description: "Not expired yet"
"""


def _make_service(yaml_content: str) -> FeatureFlagService:
    """Create a FeatureFlagService backed by a temp YAML file."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, prefix="ff_"
    )
    tmp.write(yaml_content)
    tmp.flush()
    tmp.close()
    return FeatureFlagService(config_path=tmp.name), tmp.name


def test_loads_from_yaml():
    svc, path = _make_service(SAMPLE_FLAGS_YAML)
    try:
        flags = svc.get_all()
        assert len(flags) == 6, f"Expected 6 flags, got {len(flags)}"
        assert "active_flag" in flags
        assert "disabled_flag" in flags
    finally:
        os.unlink(path)


def test_basic_boolean():
    svc, path = _make_service(SAMPLE_FLAGS_YAML)
    try:
        assert svc.is_enabled("active_flag") is True
        assert svc.is_enabled("disabled_flag") is False
        assert svc.is_enabled("nonexistent") is False
    finally:
        os.unlink(path)


def test_percentage_rollout_consistent():
    """Same tenant always gets the same result for a given flag."""
    svc, path = _make_service(SAMPLE_FLAGS_YAML)
    try:
        results = [svc.is_enabled("rollout_flag", tenant_id="test_tenant") for _ in range(100)]
        # All calls should return the same value (consistent hashing)
        assert len(set(results)) == 1, "Percentage rollout should be deterministic per tenant"
    finally:
        os.unlink(path)


def test_percentage_rollout_distribution():
    """Across many tenants, ~50% should be enabled for a 50% rollout."""
    svc, path = _make_service(SAMPLE_FLAGS_YAML)
    try:
        enabled_count = sum(
            1 for i in range(1000)
            if svc.is_enabled("rollout_flag", tenant_id=f"tenant_{i}")
        )
        # Allow generous margin: 35%-65% for 50% rollout with 1000 samples
        assert 350 <= enabled_count <= 650, (
            f"Expected ~500 enabled, got {enabled_count}"
        )
    finally:
        os.unlink(path)


def test_tenant_specific():
    svc, path = _make_service(SAMPLE_FLAGS_YAML)
    try:
        assert svc.is_enabled("tenant_flag", tenant_id="tenant_a") is True
        assert svc.is_enabled("tenant_flag", tenant_id="tenant_b") is True
        assert svc.is_enabled("tenant_flag", tenant_id="tenant_c") is False
        # No tenant_id provided with tenant-restricted flag: still enabled
        # (tenant_ids check only applies when tenant_id is given)
        assert svc.is_enabled("tenant_flag") is True
    finally:
        os.unlink(path)


def test_expired_flag():
    svc, path = _make_service(SAMPLE_FLAGS_YAML)
    try:
        assert svc.is_enabled("expired_flag") is False
        assert svc.is_enabled("future_flag") is True
    finally:
        os.unlink(path)


def test_set_flag_toggle():
    svc, path = _make_service(SAMPLE_FLAGS_YAML)
    try:
        assert svc.is_enabled("disabled_flag") is False
        svc.set_flag("disabled_flag", True)
        assert svc.is_enabled("disabled_flag") is True

        # Reload from disk to confirm persistence
        svc2 = FeatureFlagService(config_path=path)
        assert svc2.is_enabled("disabled_flag") is True
    finally:
        os.unlink(path)


def test_set_flag_new():
    svc, path = _make_service(SAMPLE_FLAGS_YAML)
    try:
        svc.set_flag("brand_new_flag", True)
        assert svc.is_enabled("brand_new_flag") is True

        svc2 = FeatureFlagService(config_path=path)
        assert svc2.is_enabled("brand_new_flag") is True
    finally:
        os.unlink(path)


def test_reload_picks_up_changes():
    svc, path = _make_service(SAMPLE_FLAGS_YAML)
    try:
        assert svc.is_enabled("disabled_flag") is False

        # Write updated YAML externally
        with open(path, "w") as f:
            f.write(SAMPLE_FLAGS_YAML.replace(
                "disabled_flag:\n    enabled: false",
                "disabled_flag:\n    enabled: true",
            ))

        svc.reload()
        assert svc.is_enabled("disabled_flag") is True
    finally:
        os.unlink(path)


def test_missing_config():
    """Service handles missing config file gracefully."""
    svc = FeatureFlagService(config_path="/tmp/nonexistent_flags_xyz.yaml")
    assert svc.is_enabled("anything") is False
    assert svc.get_all() == {}


# ═══════════════════════════════════════════════════════════════
# FAILURE CIRCUIT BREAKER TESTS
# ═══════════════════════════════════════════════════════════════


def test_breaker_trips_after_consecutive_failures():
    breaker = FailureCircuitBreaker(max_consecutive=3)
    assert breaker.is_tripped() is False

    breaker.record_failure()
    assert breaker.is_tripped() is False
    breaker.record_failure()
    assert breaker.is_tripped() is False
    breaker.record_failure()
    assert breaker.is_tripped() is True


def test_breaker_success_resets():
    breaker = FailureCircuitBreaker(max_consecutive=3)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    # Counter reset, need 3 more failures to trip
    breaker.record_failure()
    assert breaker.is_tripped() is False


def test_breaker_cooldown():
    breaker = FailureCircuitBreaker(max_consecutive=2, cooldown_seconds=0.1)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.is_tripped() is True

    time.sleep(0.15)
    # Cooldown expired, should allow retry
    assert breaker.is_tripped() is False


def test_breaker_stats():
    breaker = FailureCircuitBreaker(max_consecutive=3)
    breaker.record_success()
    breaker.record_success()
    breaker.record_failure()

    stats = breaker.stats
    assert stats["consecutive_failures"] == 1
    assert stats["total_failures"] == 1
    assert stats["total_successes"] == 2
    assert stats["is_tripped"] is False


def test_breaker_reset():
    breaker = FailureCircuitBreaker(max_consecutive=2)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.is_tripped() is True

    breaker.reset()
    assert breaker.is_tripped() is False
    assert breaker.stats["consecutive_failures"] == 0


def test_breaker_no_cooldown():
    """With cooldown=0, breaker stays tripped until manual reset or success."""
    breaker = FailureCircuitBreaker(max_consecutive=2, cooldown_seconds=0)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.is_tripped() is True
    # Even after waiting, stays tripped because cooldown is 0
    assert breaker.is_tripped() is True


# ═══════════════════════════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
