"""
Layered Config Resolution (Claurst pattern).

3-layer config: global (~/.able/config.yaml) → project (.able/config.yaml in cwd)
→ local (env vars). Each layer overrides the previous. Missing layers are skipped.

Plan item: Module 1 — Layered Config Resolution.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from threading import Lock
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

# Layer priority (lowest index = lowest priority, overridden by higher)
_GLOBAL_CONFIG_PATH = Path.home() / ".able" / "config.yaml"
_PROJECT_CONFIG_NAME = ".able/config.yaml"


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Override wins on conflicts."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _env_to_dict(prefix: str = "ABLE_") -> dict:
    """Flatten env vars with prefix into nested dict using __ as level separator.

    ABLE_ROUTING__EFFORT_LEVEL=high  →  {"routing": {"effort_level": "high"}}
    ABLE_DEBUG=true                   →  {"debug": "true"}
    """
    result: dict = {}
    for key, val in os.environ.items():
        if not key.startswith(prefix):
            continue
        # Strip prefix, lowercase, split on double-underscore for nesting
        stripped = key[len(prefix):]
        parts = stripped.lower().split("__")
        node = result
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        leaf = parts[-1]
        # Attempt type coercion for booleans and integers
        if val.lower() in ("true", "1", "yes"):
            node[leaf] = True
        elif val.lower() in ("false", "0", "no"):
            node[leaf] = False
        else:
            try:
                node[leaf] = int(val)
            except ValueError:
                try:
                    node[leaf] = float(val)
                except ValueError:
                    node[leaf] = val
    return result


def _load_yaml(path: Path) -> dict:
    """Load YAML file. Returns empty dict if missing or unreadable."""
    try:
        with open(path, "r") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            logger.warning("Config at %s is not a mapping — skipped", path)
            return {}
        return data
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning("Failed to load config %s: %s", path, exc)
        return {}


class LayeredConfig:
    """Resolve config from 3 layers: global → project → env vars.

    Usage::

        cfg = LayeredConfig()
        val = cfg.get("routing.effort_level", default="medium")
        cfg.reload()               # re-read disk layers
        full = cfg.as_dict()       # merged snapshot
    """

    def __init__(
        self,
        global_path: Optional[Path] = None,
        project_path: Optional[Path] = None,
        env_prefix: str = "ABLE_",
        cwd: Optional[Path] = None,
    ) -> None:
        self._global_path = global_path or _GLOBAL_CONFIG_PATH
        self._project_path = project_path or (
            (cwd or Path.cwd()) / _PROJECT_CONFIG_NAME
        )
        self._env_prefix = env_prefix
        self._lock = Lock()
        self._merged: dict = {}
        self._load()

    # ── public API ───────────────────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        """Resolve a config key using dot-notation.

        Example: ``cfg.get("routing.effort_level")``
        Returns *default* if the key is absent at all layers.
        """
        with self._lock:
            return self._resolve(self._merged, key, default)

    def reload(self) -> None:
        """Re-read disk layers and re-merge (env is always live)."""
        with self._lock:
            self._load()

    def as_dict(self) -> dict:
        """Return a shallow copy of the fully merged config."""
        with self._lock:
            return dict(self._merged)

    # ── internals ────────────────────────────────────────────────────────────

    def _load(self) -> None:
        """Load all 3 layers and merge (called under lock or at init)."""
        global_cfg = _load_yaml(self._global_path)
        project_cfg = _load_yaml(self._project_path)
        env_cfg = _env_to_dict(self._env_prefix)

        merged = _deep_merge(global_cfg, project_cfg)
        merged = _deep_merge(merged, env_cfg)
        self._merged = merged

    @staticmethod
    def _resolve(data: dict, key: str, default: Any) -> Any:
        """Walk dot-separated key path through nested dict."""
        parts = key.split(".")
        node: Any = data
        for part in parts:
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node
