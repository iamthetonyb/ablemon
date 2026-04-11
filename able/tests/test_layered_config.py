"""Tests for LayeredConfig — 3-layer config resolution (Claurst pattern)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from able.core.config.layered_config import LayeredConfig, _deep_merge, _env_to_dict


class TestDeepMerge:
    def test_flat_override(self):
        result = _deep_merge({"a": 1, "b": 2}, {"b": 99, "c": 3})
        assert result == {"a": 1, "b": 99, "c": 3}

    def test_nested_merge(self):
        base = {"routing": {"tier": 1, "model": "fast"}}
        override = {"routing": {"tier": 2}}
        result = _deep_merge(base, override)
        assert result["routing"]["tier"] == 2
        assert result["routing"]["model"] == "fast"

    def test_non_dict_override(self):
        # Non-dict value at key replaces entirely
        result = _deep_merge({"x": {"a": 1}}, {"x": "string"})
        assert result["x"] == "string"

    def test_empty_override(self):
        base = {"a": 1}
        assert _deep_merge(base, {}) == {"a": 1}

    def test_empty_base(self):
        assert _deep_merge({}, {"a": 1}) == {"a": 1}


class TestEnvToDict:
    def setup_method(self):
        # Clean slate
        for k in list(os.environ.keys()):
            if k.startswith("ABLE_"):
                del os.environ[k]

    def test_simple_key(self):
        os.environ["ABLE_DEBUG"] = "true"
        d = _env_to_dict("ABLE_")
        assert d["debug"] is True

    def test_nested_key(self):
        os.environ["ABLE_ROUTING__EFFORT_LEVEL"] = "high"
        d = _env_to_dict("ABLE_")
        assert d["routing"]["effort_level"] == "high"

    def test_integer_coercion(self):
        os.environ["ABLE_MAX_ITER"] = "20"
        d = _env_to_dict("ABLE_")
        assert d["max_iter"] == 20

    def test_false_coercion(self):
        os.environ["ABLE_ENABLED"] = "false"
        d = _env_to_dict("ABLE_")
        assert d["enabled"] is False

    def test_non_matching_prefix_ignored(self):
        os.environ["OTHER_VAR"] = "yes"
        d = _env_to_dict("ABLE_")
        assert "other_var" not in d


class TestLayeredConfig:
    def _write_yaml(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as fh:
            yaml.safe_dump(data, fh)

    def setup_method(self):
        for k in list(os.environ.keys()):
            if k.startswith("ABLE_"):
                del os.environ[k]

    def test_empty_layers_returns_default(self, tmp_path):
        cfg = LayeredConfig(
            global_path=tmp_path / "nonexistent.yaml",
            project_path=tmp_path / "also_nonexistent.yaml",
        )
        assert cfg.get("routing.tier", default=1) == 1

    def test_global_layer_loaded(self, tmp_path):
        gpath = tmp_path / "global.yaml"
        self._write_yaml(gpath, {"routing": {"tier": 2}})
        cfg = LayeredConfig(global_path=gpath, project_path=tmp_path / "none.yaml")
        assert cfg.get("routing.tier") == 2

    def test_project_overrides_global(self, tmp_path):
        gpath = tmp_path / "global.yaml"
        ppath = tmp_path / "project.yaml"
        self._write_yaml(gpath, {"routing": {"tier": 1, "model": "fast"}})
        self._write_yaml(ppath, {"routing": {"tier": 3}})
        cfg = LayeredConfig(global_path=gpath, project_path=ppath)
        assert cfg.get("routing.tier") == 3
        assert cfg.get("routing.model") == "fast"  # not overridden

    def test_env_overrides_project(self, tmp_path):
        ppath = tmp_path / "project.yaml"
        self._write_yaml(ppath, {"routing": {"tier": 2}})
        os.environ["ABLE_ROUTING__TIER"] = "5"
        cfg = LayeredConfig(global_path=tmp_path / "none.yaml", project_path=ppath)
        assert cfg.get("routing.tier") == 5

    def test_dot_notation_nested(self, tmp_path):
        gpath = tmp_path / "global.yaml"
        self._write_yaml(gpath, {"a": {"b": {"c": "deep"}}})
        cfg = LayeredConfig(global_path=gpath, project_path=tmp_path / "none.yaml")
        assert cfg.get("a.b.c") == "deep"

    def test_missing_key_returns_default(self, tmp_path):
        cfg = LayeredConfig(
            global_path=tmp_path / "none.yaml",
            project_path=tmp_path / "none.yaml",
        )
        assert cfg.get("does.not.exist", default="fallback") == "fallback"

    def test_reload_picks_up_changes(self, tmp_path):
        gpath = tmp_path / "global.yaml"
        self._write_yaml(gpath, {"value": 1})
        cfg = LayeredConfig(global_path=gpath, project_path=tmp_path / "none.yaml")
        assert cfg.get("value") == 1
        self._write_yaml(gpath, {"value": 99})
        cfg.reload()
        assert cfg.get("value") == 99

    def test_as_dict_returns_merged(self, tmp_path):
        gpath = tmp_path / "global.yaml"
        ppath = tmp_path / "project.yaml"
        self._write_yaml(gpath, {"a": 1})
        self._write_yaml(ppath, {"b": 2})
        cfg = LayeredConfig(global_path=gpath, project_path=ppath)
        d = cfg.as_dict()
        assert d["a"] == 1
        assert d["b"] == 2

    def test_invalid_yaml_skipped_gracefully(self, tmp_path):
        gpath = tmp_path / "bad.yaml"
        gpath.write_text("key: [unclosed bracket")
        cfg = LayeredConfig(global_path=gpath, project_path=tmp_path / "none.yaml")
        # Should not raise — bad YAML file is skipped
        assert cfg.get("key", default="default") == "default"
