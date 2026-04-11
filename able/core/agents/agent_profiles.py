"""
Named Agent Profiles (Claurst pattern).

Defines typed agent capability envelopes loaded from
config/agent_profiles.yaml with built-in fallback defaults.

Plan item: Module 2 — Named Agent Profiles.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_PROFILES_PATH = Path(__file__).resolve().parents[3] / "config" / "agent_profiles.yaml"

# ── Built-in profile definitions (used when YAML is absent) ──────────────────

_BUILTIN_PROFILES: list[dict] = [
    {
        "name": "default",
        "description": "Full-capability agent — all tools, maximum tier.",
        "allowed_tools": ["*"],
        "max_tier": 4,
        "max_iterations": 20,
        "system_prompt_suffix": "",
    },
    {
        "name": "researcher",
        "description": "Search and read operations only — no writes or code exec.",
        "allowed_tools": ["web_search", "read_file", "list_files", "grep_files"],
        "max_tier": 2,
        "max_iterations": 10,
        "system_prompt_suffix": (
            "You are operating in researcher mode. "
            "Gather information only — do not create, edit, or delete files."
        ),
    },
    {
        "name": "coder",
        "description": "Code-focused agent — write/edit code files and run tests.",
        "allowed_tools": [
            "read_file", "write_file", "edit_file", "run_command",
            "list_files", "grep_files",
        ],
        "max_tier": 4,
        "max_iterations": 15,
        "system_prompt_suffix": (
            "You are operating in coder mode. "
            "Focus on code quality, tests, and clean diffs."
        ),
    },
    {
        "name": "observer",
        "description": "Read-only agent — minimal tier, no writes.",
        "allowed_tools": ["read_file", "list_files", "grep_files"],
        "max_tier": 1,
        "max_iterations": 5,
        "system_prompt_suffix": (
            "You are operating in observer mode. "
            "You may only read files and report findings."
        ),
    },
]


@dataclass
class AgentProfile:
    """Capability envelope for a named agent role."""

    name: str
    description: str
    allowed_tools: List[str] = field(default_factory=lambda: ["*"])
    max_tier: int = 4
    max_iterations: int = 20
    system_prompt_suffix: str = ""

    def allows_tool(self, tool_name: str) -> bool:
        """True if this profile permits the given tool."""
        if "*" in self.allowed_tools:
            return True
        return tool_name in self.allowed_tools


class ProfileRegistry:
    """Load and serve AgentProfile objects.

    Loads from *profiles_path* (YAML) and merges with built-in defaults.
    YAML entries override built-ins; extra YAML profiles are added.

    Usage::

        registry = ProfileRegistry()
        profile = registry.get_profile("researcher")
        names = registry.list_profiles()
    """

    def __init__(self, profiles_path: Optional[Path] = None) -> None:
        self._path = profiles_path or _DEFAULT_PROFILES_PATH
        self._profiles: dict[str, AgentProfile] = {}
        self._load()

    # ── public API ───────────────────────────────────────────────────────────

    def get_profile(self, name: str) -> AgentProfile:
        """Return named profile, falling back to 'default'."""
        return self._profiles.get(name, self._profiles["default"])

    def list_profiles(self) -> list[str]:
        """Return all registered profile names."""
        return sorted(self._profiles.keys())

    def reload(self) -> None:
        """Re-read YAML and rebuild registry."""
        self._load()

    # ── internals ────────────────────────────────────────────────────────────

    def _load(self) -> None:
        # Start from built-ins
        profiles: dict[str, AgentProfile] = {}
        for raw in _BUILTIN_PROFILES:
            p = _profile_from_dict(raw)
            profiles[p.name] = p

        # Override / extend with YAML
        yaml_profiles = self._load_yaml()
        for raw in yaml_profiles:
            try:
                p = _profile_from_dict(raw)
                profiles[p.name] = p
            except (KeyError, TypeError) as exc:
                logger.warning("Skipping malformed agent profile entry: %s", exc)

        self._profiles = profiles

    def _load_yaml(self) -> list[dict]:
        try:
            with open(self._path, "r") as fh:
                data = yaml.safe_load(fh) or {}
            entries = data.get("profiles", [])
            if not isinstance(entries, list):
                logger.warning("agent_profiles.yaml 'profiles' key is not a list")
                return []
            return entries
        except FileNotFoundError:
            return []
        except Exception as exc:
            logger.warning("Failed to load agent_profiles.yaml: %s", exc)
            return []


def _profile_from_dict(raw: dict) -> AgentProfile:
    return AgentProfile(
        name=raw["name"],
        description=raw.get("description", ""),
        allowed_tools=raw.get("allowed_tools", ["*"]),
        max_tier=int(raw.get("max_tier", 4)),
        max_iterations=int(raw.get("max_iterations", 20)),
        system_prompt_suffix=raw.get("system_prompt_suffix", ""),
    )
