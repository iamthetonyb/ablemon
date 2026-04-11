"""Tests for F10 — Config Validation at Boot.

Covers: routing config, scorer weights, tool permissions, env references,
regex audit, report structure.
"""

import os
import pytest
from unittest.mock import patch

from able.core.gateway.config_validator import (
    ConfigValidator,
    ValidationIssue,
    ValidationReport,
)


@pytest.fixture
def validator(tmp_path):
    return ConfigValidator(config_dir=str(tmp_path))


# ── Routing config validation ────────────────────────────────────

class TestRoutingConfig:

    def test_valid_config(self, validator, tmp_path):
        (tmp_path / "routing_config.yaml").write_text("""
providers:
  - name: test-provider
    provider_type: openai
    model_id: gpt-4
    cost_per_m_input: 30.0
    cost_per_m_output: 60.0
""")
        report = validator.validate_all()
        assert report.valid

    def test_missing_required_field(self, validator, tmp_path):
        (tmp_path / "routing_config.yaml").write_text("""
providers:
  - provider_type: openai
""")
        report = validator.validate_all()
        errors = [i for i in report.errors if "name" in i.field]
        assert len(errors) >= 1

    def test_invalid_cost_type(self, validator, tmp_path):
        (tmp_path / "routing_config.yaml").write_text("""
providers:
  - name: test
    provider_type: openai
    cost_per_m_input: "not a number"
""")
        report = validator.validate_all()
        errors = [i for i in report.errors if "cost" in i.field]
        assert len(errors) >= 1

    def test_missing_env_var_warning(self, validator, tmp_path):
        (tmp_path / "routing_config.yaml").write_text("""
providers:
  - name: test
    provider_type: openai
    api_key_env: NONEXISTENT_KEY_12345
""")
        env = os.environ.copy()
        env.pop("NONEXISTENT_KEY_12345", None)
        with patch.dict(os.environ, env, clear=True):
            report = validator.validate_all()
        warnings = [i for i in report.warnings if "NONEXISTENT" in i.message]
        assert len(warnings) >= 1

    def test_empty_yaml(self, validator, tmp_path):
        (tmp_path / "routing_config.yaml").write_text("")
        report = validator.validate_all()
        assert not report.valid

    def test_non_dict_root(self, validator, tmp_path):
        (tmp_path / "routing_config.yaml").write_text("- just\n- a\n- list\n")
        report = validator.validate_all()
        assert not report.valid


# ── Scorer weights validation ────────────────────────────────────

class TestScorerWeights:

    def test_valid_weights(self, validator, tmp_path):
        (tmp_path / "scorer_weights.yaml").write_text("""
weights:
  complexity: 0.5
  length: 0.3
  tool_count: 0.2
""")
        report = validator.validate_all()
        assert report.valid

    def test_out_of_range_weight(self, validator, tmp_path):
        (tmp_path / "scorer_weights.yaml").write_text("""
weights:
  complexity: 50
""")
        report = validator.validate_all()
        warnings = [i for i in report.warnings if "outside" in i.message]
        assert len(warnings) >= 1


# ── Tool permissions validation ──────────────────────────────────

class TestToolPermissions:

    def test_valid_permissions(self, validator, tmp_path):
        (tmp_path / "tool_permissions.yaml").write_text("""
always_allow:
  - git status
  - echo
ask_before:
  - rm
never_allow:
  - rm -rf /
""")
        report = validator.validate_all()
        assert report.valid

    def test_unknown_section(self, validator, tmp_path):
        (tmp_path / "tool_permissions.yaml").write_text("""
always_allow:
  - echo
sometimes_allow:
  - rm
""")
        report = validator.validate_all()
        warnings = [i for i in report.warnings if "sometimes_allow" in i.message]
        assert len(warnings) >= 1


# ── Env reference checking ───────────────────────────────────────

class TestEnvReferences:

    def test_unresolved_env_ref(self, validator, tmp_path):
        (tmp_path / "routing_config.yaml").write_text("""
providers:
  - name: test
    provider_type: openai
    endpoint: "https://${MISSING_HOST}/v1"
""")
        env = os.environ.copy()
        env.pop("MISSING_HOST", None)
        with patch.dict(os.environ, env, clear=True):
            report = validator.validate_all()
        warnings = [i for i in report.warnings if "MISSING_HOST" in i.message]
        assert len(warnings) >= 1

    def test_env_ref_with_default_ok(self, validator, tmp_path):
        (tmp_path / "routing_config.yaml").write_text("""
providers:
  - name: test
    provider_type: openai
    endpoint: "https://${HOST:-localhost}/v1"
""")
        report = validator.validate_all()
        # Should not warn because there's a default
        env_warnings = [i for i in report.warnings if "HOST" in i.message and "Unresolved" in i.message]
        assert len(env_warnings) == 0


# ── Regex audit ──────────────────────────────────────────────────

class TestRegexAudit:

    def test_valid_regex(self):
        issues = ConfigValidator._audit_regex("test.yaml", "rule", "^git\\s+status$")
        assert len(issues) == 0

    def test_invalid_regex(self):
        issues = ConfigValidator._audit_regex("test.yaml", "rule", "[invalid")
        assert len(issues) == 1
        assert issues[0].severity == "error"

    def test_long_regex_warning(self):
        issues = ConfigValidator._audit_regex("test.yaml", "rule", "a" * 600)
        assert any(i.severity == "warning" and "long" in i.message for i in issues)


# ── Report structure ─────────────────────────────────────────────

class TestReport:

    def test_empty_report_valid(self):
        r = ValidationReport()
        assert r.valid

    def test_report_with_errors(self):
        r = ValidationReport(issues=[
            ValidationIssue("f", "x", "error", "broken"),
        ])
        assert not r.valid
        assert r.errors == r.issues

    def test_summary(self, validator):
        report = validator.validate_all()
        s = report.summary()
        assert "Config validation" in s
        assert "files checked" in s

    def test_no_files_ok(self, validator):
        report = validator.validate_all()
        assert report.valid
        assert report.files_checked == 0
