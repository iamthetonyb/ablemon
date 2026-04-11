"""Tests for E6 — Real-Time Config Reload.

Covers: env var substitution, hash-based change detection, tool_permissions
reload, ${VAR:-default} syntax, recursive substitution.
"""

import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from able.core.routing.provider_registry import ProviderRegistry, ProviderTierConfig


# ── Environment variable substitution ─────────────────────────────

class TestEnvVarSubstitution:

    def test_simple_substitution(self):
        os.environ["TEST_API_KEY"] = "sk-test-12345"
        data = {"api_key": "${TEST_API_KEY}"}
        result = ProviderRegistry._substitute_env_vars(data)
        assert result["api_key"] == "sk-test-12345"
        del os.environ["TEST_API_KEY"]

    def test_default_value(self):
        # Ensure the var doesn't exist
        os.environ.pop("NONEXISTENT_VAR_XYZ", None)
        data = {"endpoint": "${NONEXISTENT_VAR_XYZ:-https://fallback.api.com}"}
        result = ProviderRegistry._substitute_env_vars(data)
        assert result["endpoint"] == "https://fallback.api.com"

    def test_default_empty(self):
        os.environ.pop("MISSING_VAR_ABC", None)
        data = {"key": "${MISSING_VAR_ABC}"}
        result = ProviderRegistry._substitute_env_vars(data)
        assert result["key"] == ""

    def test_nested_dict_substitution(self):
        os.environ["NEST_TEST"] = "nested_value"
        data = {"outer": {"inner": {"key": "${NEST_TEST}"}}}
        result = ProviderRegistry._substitute_env_vars(data)
        assert result["outer"]["inner"]["key"] == "nested_value"
        del os.environ["NEST_TEST"]

    def test_list_substitution(self):
        os.environ["LIST_TEST"] = "item_value"
        data = {"items": ["${LIST_TEST}", "static", "${LIST_TEST}"]}
        result = ProviderRegistry._substitute_env_vars(data)
        assert result["items"] == ["item_value", "static", "item_value"]
        del os.environ["LIST_TEST"]

    def test_non_string_passthrough(self):
        data = {"count": 42, "enabled": True, "rate": 3.14}
        result = ProviderRegistry._substitute_env_vars(data)
        assert result == data

    def test_mixed_text_and_var(self):
        os.environ["MIX_HOST"] = "api.example.com"
        data = {"url": "https://${MIX_HOST}/v1/chat"}
        result = ProviderRegistry._substitute_env_vars(data)
        assert result["url"] == "https://api.example.com/v1/chat"
        del os.environ["MIX_HOST"]

    def test_multiple_vars_in_one_string(self):
        os.environ["PROTO"] = "https"
        os.environ["HOST_E6"] = "example.com"
        data = {"url": "${PROTO}://${HOST_E6}"}
        result = ProviderRegistry._substitute_env_vars(data)
        assert result["url"] == "https://example.com"
        del os.environ["PROTO"]
        del os.environ["HOST_E6"]

    def test_no_vars_passthrough(self):
        data = {"key": "no variables here"}
        result = ProviderRegistry._substitute_env_vars(data)
        assert result["key"] == "no variables here"

    def test_empty_data(self):
        assert ProviderRegistry._substitute_env_vars({}) == {}
        assert ProviderRegistry._substitute_env_vars("plain") == "plain"
        assert ProviderRegistry._substitute_env_vars(None) is None


# ── YAML loading with env vars ────────────────────────────────────

class TestYAMLLoading:

    def test_from_yaml_with_env_vars(self, tmp_path):
        os.environ["E6_MODEL_ID"] = "gpt-5.4-test"
        config = tmp_path / "routing.yaml"
        config.write_text("""
providers:
  - name: test-provider
    tier: 1
    provider_type: ollama
    model_id: "${E6_MODEL_ID}"
    cost_per_m_input: 0
    cost_per_m_output: 0
    max_context: 128000
""")
        registry = ProviderRegistry.from_yaml(config)
        p = registry.get_provider_config("test-provider")
        assert p is not None
        assert p.model_id == "gpt-5.4-test"
        del os.environ["E6_MODEL_ID"]

    def test_from_yaml_default_fallback(self, tmp_path):
        os.environ.pop("E6_MISSING_KEY", None)
        config = tmp_path / "routing.yaml"
        config.write_text("""
providers:
  - name: fallback-test
    tier: 1
    provider_type: ollama
    model_id: "${E6_MISSING_KEY:-default-model}"
    cost_per_m_input: 0
    cost_per_m_output: 0
    max_context: 128000
""")
        registry = ProviderRegistry.from_yaml(config)
        p = registry.get_provider_config("fallback-test")
        assert p.model_id == "default-model"

    def test_from_yaml_nonexistent_file(self, tmp_path):
        registry = ProviderRegistry.from_yaml(tmp_path / "nope.yaml")
        assert len(registry._providers) == 0


# ── Hash-based reload detection ──────────────────────────────────

class TestReloadDetection:

    def test_reload_detects_change(self, tmp_path):
        config = tmp_path / "routing.yaml"
        config.write_text("""
providers:
  - name: p1
    tier: 1
    provider_type: ollama
    model_id: model-a
    cost_per_m_input: 0
    cost_per_m_output: 0
    max_context: 128000
""")
        registry = ProviderRegistry.from_yaml(config)
        assert registry.get_provider_config("p1").model_id == "model-a"

        # Modify
        config.write_text("""
providers:
  - name: p1
    tier: 1
    provider_type: ollama
    model_id: model-b
    cost_per_m_input: 0
    cost_per_m_output: 0
    max_context: 128000
""")
        reloaded = registry.reload_from_yaml(config)
        assert reloaded is True
        assert registry.get_provider_config("p1").model_id == "model-b"

    def test_no_reload_when_unchanged(self, tmp_path):
        config = tmp_path / "routing.yaml"
        config.write_text("providers: []")
        registry = ProviderRegistry.from_yaml(config)
        assert registry.reload_from_yaml(config) is False

    def test_reload_nonexistent_returns_false(self, tmp_path):
        registry = ProviderRegistry([])
        assert registry.reload_from_yaml(tmp_path / "missing.yaml") is False
