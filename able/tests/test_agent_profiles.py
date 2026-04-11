"""Tests for AgentProfile / ProfileRegistry (Claurst pattern)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from able.core.agents.agent_profiles import (
    AgentProfile,
    ProfileRegistry,
    _profile_from_dict,
)


class TestAgentProfile:
    def test_allows_tool_wildcard(self):
        p = AgentProfile(name="default", description="", allowed_tools=["*"])
        assert p.allows_tool("anything")
        assert p.allows_tool("read_file")

    def test_allows_tool_explicit_list(self):
        p = AgentProfile(
            name="coder", description="", allowed_tools=["read_file", "write_file"]
        )
        assert p.allows_tool("read_file")
        assert p.allows_tool("write_file")
        assert not p.allows_tool("run_sql")

    def test_profile_from_dict(self):
        raw = {
            "name": "test",
            "description": "desc",
            "allowed_tools": ["search"],
            "max_tier": 2,
            "max_iterations": 8,
            "system_prompt_suffix": "Be brief.",
        }
        p = _profile_from_dict(raw)
        assert p.name == "test"
        assert p.max_tier == 2
        assert p.max_iterations == 8
        assert p.system_prompt_suffix == "Be brief."

    def test_profile_from_dict_defaults(self):
        p = _profile_from_dict({"name": "minimal", "description": ""})
        assert p.allowed_tools == ["*"]
        assert p.max_tier == 4
        assert p.max_iterations == 20
        assert p.system_prompt_suffix == ""


class TestProfileRegistry:
    def test_builtin_defaults_exist(self):
        reg = ProfileRegistry(profiles_path=Path("/nonexistent/profiles.yaml"))
        names = reg.list_profiles()
        assert "default" in names
        assert "researcher" in names
        assert "coder" in names
        assert "observer" in names

    def test_get_default_profile(self):
        reg = ProfileRegistry(profiles_path=Path("/nonexistent/profiles.yaml"))
        p = reg.get_profile("default")
        assert p.name == "default"
        assert "*" in p.allowed_tools
        assert p.max_tier == 4
        assert p.max_iterations == 20

    def test_get_researcher_profile(self):
        reg = ProfileRegistry(profiles_path=Path("/nonexistent/profiles.yaml"))
        p = reg.get_profile("researcher")
        assert p.max_tier == 2
        assert p.max_iterations == 10
        assert not p.allows_tool("write_file")
        assert p.allows_tool("web_search")

    def test_get_observer_profile(self):
        reg = ProfileRegistry(profiles_path=Path("/nonexistent/profiles.yaml"))
        p = reg.get_profile("observer")
        assert p.max_tier == 1
        assert p.max_iterations == 5
        assert not p.allows_tool("write_file")
        assert p.allows_tool("read_file")

    def test_unknown_profile_falls_back_to_default(self):
        reg = ProfileRegistry(profiles_path=Path("/nonexistent/profiles.yaml"))
        p = reg.get_profile("nonexistent_profile")
        assert p.name == "default"

    def test_yaml_profile_overrides_builtin(self, tmp_path):
        yaml_path = tmp_path / "agent_profiles.yaml"
        data = {
            "profiles": [
                {
                    "name": "researcher",
                    "description": "Custom researcher",
                    "allowed_tools": ["web_search"],
                    "max_tier": 3,
                    "max_iterations": 7,
                }
            ]
        }
        with open(yaml_path, "w") as fh:
            yaml.safe_dump(data, fh)

        reg = ProfileRegistry(profiles_path=yaml_path)
        p = reg.get_profile("researcher")
        assert p.max_tier == 3
        assert p.max_iterations == 7
        assert p.description == "Custom researcher"

    def test_yaml_extra_profile_added(self, tmp_path):
        yaml_path = tmp_path / "agent_profiles.yaml"
        data = {
            "profiles": [
                {
                    "name": "custom",
                    "description": "A custom profile",
                    "allowed_tools": ["special_tool"],
                    "max_tier": 2,
                    "max_iterations": 6,
                }
            ]
        }
        with open(yaml_path, "w") as fh:
            yaml.safe_dump(data, fh)

        reg = ProfileRegistry(profiles_path=yaml_path)
        assert "custom" in reg.list_profiles()
        p = reg.get_profile("custom")
        assert p.allows_tool("special_tool")
        assert not p.allows_tool("read_file")

    def test_reload_picks_up_new_profile(self, tmp_path):
        yaml_path = tmp_path / "agent_profiles.yaml"
        with open(yaml_path, "w") as fh:
            yaml.safe_dump({"profiles": []}, fh)
        reg = ProfileRegistry(profiles_path=yaml_path)
        assert "newbie" not in reg.list_profiles()

        data = {"profiles": [{"name": "newbie", "description": "new"}]}
        with open(yaml_path, "w") as fh:
            yaml.safe_dump(data, fh)
        reg.reload()
        assert "newbie" in reg.list_profiles()

    def test_malformed_yaml_entry_skipped(self, tmp_path):
        yaml_path = tmp_path / "agent_profiles.yaml"
        # Entry missing required 'name' key
        data = {"profiles": [{"description": "no name here"}]}
        with open(yaml_path, "w") as fh:
            yaml.safe_dump(data, fh)
        # Should not raise
        reg = ProfileRegistry(profiles_path=yaml_path)
        assert "default" in reg.list_profiles()

    def test_list_profiles_sorted(self):
        reg = ProfileRegistry(profiles_path=Path("/nonexistent/profiles.yaml"))
        names = reg.list_profiles()
        assert names == sorted(names)
