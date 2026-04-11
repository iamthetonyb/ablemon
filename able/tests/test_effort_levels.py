"""Tests for effort levels — user routing control (Claurst pattern)."""

import os
import pytest
from able.core.routing.effort_levels import (
    EffortLevel, EffortOverride, get_effort_level, apply_effort, is_session_scoped,
)


class TestEffortLevel:

    def test_enum_values(self):
        assert EffortLevel.LOW == "low"
        assert EffortLevel.MEDIUM == "medium"
        assert EffortLevel.HIGH == "high"
        assert EffortLevel.MAX == "max"


class TestGetEffortLevel:

    def test_default_is_medium(self):
        os.environ.pop("ABLE_EFFORT_LEVEL", None)
        assert get_effort_level() == EffortLevel.MEDIUM

    def test_reads_env(self):
        os.environ["ABLE_EFFORT_LEVEL"] = "high"
        try:
            assert get_effort_level() == EffortLevel.HIGH
        finally:
            del os.environ["ABLE_EFFORT_LEVEL"]

    def test_invalid_env_returns_medium(self):
        os.environ["ABLE_EFFORT_LEVEL"] = "turbo"
        try:
            assert get_effort_level() == EffortLevel.MEDIUM
        finally:
            del os.environ["ABLE_EFFORT_LEVEL"]


class TestApplyEffort:

    def test_low_forces_tier_1(self):
        result = apply_effort(0.8, EffortLevel.LOW)
        assert result.forced_tier == 1
        assert result.level == EffortLevel.LOW

    def test_max_forces_tier_4(self):
        result = apply_effort(0.2, EffortLevel.MAX)
        assert result.forced_tier == 4

    def test_medium_no_change(self):
        result = apply_effort(0.5, EffortLevel.MEDIUM)
        assert result.forced_tier is None
        assert result.adjusted_score == 0.5

    def test_high_biases_up(self):
        result = apply_effort(0.5, EffortLevel.HIGH)
        assert result.forced_tier is None
        assert result.adjusted_score > 0.5

    def test_high_caps_at_1(self):
        result = apply_effort(0.95, EffortLevel.HIGH)
        assert result.adjusted_score <= 1.0

    def test_preserves_original_score(self):
        result = apply_effort(0.6, EffortLevel.HIGH)
        assert result.original_score == 0.6


class TestSessionScoped:

    def test_max_is_session_scoped(self):
        assert is_session_scoped(EffortLevel.MAX) is True

    def test_others_are_persistent(self):
        assert is_session_scoped(EffortLevel.LOW) is False
        assert is_session_scoped(EffortLevel.MEDIUM) is False
        assert is_session_scoped(EffortLevel.HIGH) is False
