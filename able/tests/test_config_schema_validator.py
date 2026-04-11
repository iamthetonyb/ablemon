"""Tests for able.core.config.config_validator — schema validation (Claurst + Hermes v0.8)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from able.core.config.config_validator import (
    ValidationError,
    ValidationResult,
    validate_config,
    validate_all_configs,
)


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        yaml.safe_dump(data, fh)


class TestValidationResult:
    def test_valid_when_no_errors(self):
        r = ValidationResult(valid=True)
        assert r.valid

    def test_invalid_when_errors_list_non_empty(self):
        r = ValidationResult(
            valid=False,
            errors=[ValidationError("f", "bad")],
        )
        assert not r.valid

    def test_all_issues_combines_errors_and_warnings(self):
        r = ValidationResult(
            valid=False,
            errors=[ValidationError("a", "err")],
            warnings=[ValidationError("b", "warn", severity="warning")],
        )
        assert len(r.all_issues()) == 2


class TestValidateConfigDispatch:
    def test_missing_file_returns_invalid(self, tmp_path):
        result = validate_config(tmp_path / "nonexistent_routing_config.yaml")
        assert not result.valid
        assert any("not found" in e.message.lower() for e in result.errors)

    def test_bad_yaml_returns_invalid(self, tmp_path):
        p = tmp_path / "routing_config.yaml"
        p.write_text("key: [unclosed bracket")
        result = validate_config(p)
        assert not result.valid

    def test_unknown_file_returns_valid_with_warning(self, tmp_path):
        p = tmp_path / "unknown_file.yaml"
        _write(p, {"x": 1})
        result = validate_config(p)
        assert result.valid
        assert len(result.warnings) == 1


class TestRoutingConfigValidation:
    def test_valid_minimal_provider(self, tmp_path):
        p = tmp_path / "routing_config.yaml"
        _write(p, {
            "providers": [
                {
                    "name": "p1", "tier": 1, "provider_type": "openai",
                    "model_id": "gpt-4", "enabled": True,
                    "cost_per_m_input": 0.0, "cost_per_m_output": 0.0,
                }
            ]
        })
        result = validate_config(p)
        assert result.valid, [str(e) for e in result.errors]

    def test_missing_required_fields_reported(self, tmp_path):
        p = tmp_path / "routing_config.yaml"
        # Missing tier, model_id, enabled
        _write(p, {"providers": [{"name": "p1", "provider_type": "openai"}]})
        result = validate_config(p)
        assert not result.valid
        missing = {e.field for e in result.errors}
        assert any("tier" in f for f in missing)
        assert any("model_id" in f for f in missing)
        assert any("enabled" in f for f in missing)

    def test_tier_out_of_range_is_error(self, tmp_path):
        p = tmp_path / "routing_config.yaml"
        _write(p, {
            "providers": [
                {"name": "p1", "tier": 9, "provider_type": "openai",
                 "model_id": "gpt-4", "enabled": True}
            ]
        })
        result = validate_config(p)
        assert not result.valid
        assert any("tier" in e.field for e in result.errors)

    def test_negative_cost_is_error(self, tmp_path):
        p = tmp_path / "routing_config.yaml"
        _write(p, {
            "providers": [
                {"name": "p1", "tier": 1, "provider_type": "openai",
                 "model_id": "m", "enabled": True, "cost_per_m_input": -5.0}
            ]
        })
        result = validate_config(p)
        assert not result.valid

    def test_missing_providers_key_is_error(self, tmp_path):
        p = tmp_path / "routing_config.yaml"
        _write(p, {"other_key": "value"})
        result = validate_config(p)
        assert not result.valid


class TestScorerWeightsValidation:
    def test_weights_summing_to_one_passes(self, tmp_path):
        p = tmp_path / "scorer_weights.yaml"
        _write(p, {
            "features": {
                "token_count_weight": 0.15,
                "requires_tools_weight": 0.15,
                "requires_code_weight": 0.20,
                "multi_step_weight": 0.20,
                "safety_critical_weight": 0.30,
            }
        })
        result = validate_config(p)
        assert result.valid
        assert len(result.warnings) == 0

    def test_weights_not_summing_to_one_warns(self, tmp_path):
        p = tmp_path / "scorer_weights.yaml"
        _write(p, {
            "features": {
                "a_weight": 0.5,
                "b_weight": 0.5,
                "c_weight": 0.5,   # sum = 1.5
            }
        })
        result = validate_config(p)
        assert result.valid  # warning-only, not invalid
        assert any("sum" in w.message.lower() for w in result.warnings)

    def test_negative_weight_is_error(self, tmp_path):
        p = tmp_path / "scorer_weights.yaml"
        _write(p, {"features": {"bad_weight": -0.1}})
        result = validate_config(p)
        assert not result.valid


class TestToolPermissionsValidation:
    def test_valid_no_overlap(self, tmp_path):
        p = tmp_path / "tool_permissions.yaml"
        _write(p, {
            "always_allow": ["ls", "cat"],
            "ask_before": ["git push"],
            "never_allow": ["rm -rf /"],
        })
        result = validate_config(p)
        assert result.valid

    def test_always_and_never_overlap_is_error(self, tmp_path):
        p = tmp_path / "tool_permissions.yaml"
        _write(p, {
            "always_allow": ["ls", "dangerous_cmd"],
            "never_allow": ["dangerous_cmd"],
        })
        result = validate_config(p)
        assert not result.valid
        assert any("dangerous_cmd" in e.message for e in result.errors)

    def test_ask_and_always_overlap_is_warning(self, tmp_path):
        p = tmp_path / "tool_permissions.yaml"
        _write(p, {
            "always_allow": ["git push"],
            "ask_before": ["git push"],
        })
        result = validate_config(p)
        assert result.valid  # warning, not error
        assert any("git push" in w.message for w in result.warnings)


class TestValidateAllConfigs:
    def test_checks_all_three_files(self, tmp_path):
        for name, data in [
            ("routing_config.yaml", {
                "providers": [
                    {"name": "p1", "tier": 1, "provider_type": "openai",
                     "model_id": "m", "enabled": True}
                ]
            }),
            ("scorer_weights.yaml", {"features": {"a_weight": 1.0}}),
            ("tool_permissions.yaml", {"always_allow": ["ls"], "never_allow": ["rm"]}),
        ]:
            _write(tmp_path / name, data)

        results = validate_all_configs(tmp_path)
        assert set(results.keys()) == {
            "routing_config.yaml", "scorer_weights.yaml", "tool_permissions.yaml"
        }

    def test_missing_files_dont_crash(self, tmp_path):
        # Empty dir — no files present
        results = validate_all_configs(tmp_path)
        # All 3 keys present, each reporting "not found"
        assert len(results) == 3
        for name, r in results.items():
            assert not r.valid
