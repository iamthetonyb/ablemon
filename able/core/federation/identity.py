"""Instance identity and network enrollment for federated distillation."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_ABLE_HOME = Path.home() / ".able"
_INSTANCE_FILE = "instance.yaml"


def _load_yaml(path: Path) -> Dict[str, Any]:
    """Load a YAML file, returning empty dict if missing or broken."""
    if not path.exists():
        return {}
    try:
        import yaml  # noqa: delayed import — yaml not always available

        with open(path, "r") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        # Fallback: parse simple key: value lines
        return _parse_simple_yaml(path)


def _parse_simple_yaml(path: Path) -> Dict[str, Any]:
    """Minimal YAML parser for flat key: value files (no yaml dependency)."""
    result: Dict[str, Any] = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            val = val.strip()
            if val == "null" or val == "~":
                result[key.strip()] = None
            elif val == "true":
                result[key.strip()] = True
            elif val == "false":
                result[key.strip()] = False
            elif val.startswith("[") and val.endswith("]"):
                # Simple list: [a, b, c]
                inner = val[1:-1].strip()
                result[key.strip()] = (
                    [v.strip().strip("'\"") for v in inner.split(",") if v.strip()]
                    if inner
                    else []
                )
            else:
                val = val.strip("'\"")
                result[key.strip()] = val
    except Exception:
        pass
    return result


def _save_yaml(path: Path, data: Dict[str, Any]) -> None:
    """Save a dict as YAML (or simple key: value if yaml unavailable)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml

        with open(path, "w") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
    except ImportError:
        # Fallback: write simple key: value
        lines = []
        for k, v in data.items():
            if v is None:
                lines.append(f"{k}: null")
            elif isinstance(v, bool):
                lines.append(f"{k}: {'true' if v else 'false'}")
            elif isinstance(v, list):
                items = ", ".join(str(i) for i in v)
                lines.append(f"{k}: [{items}]")
            else:
                lines.append(f"{k}: {v}")
        path.write_text("\n".join(lines) + "\n")


def get_or_create_instance_id(able_home: Optional[Path] = None) -> str:
    """Load instance_id from instance.yaml, or generate and persist one."""
    home = able_home or _DEFAULT_ABLE_HOME
    home.mkdir(parents=True, exist_ok=True)
    config_path = home / _INSTANCE_FILE

    config = _load_yaml(config_path)
    if config.get("instance_id"):
        return config["instance_id"]

    # Generate new identity
    instance_id = str(uuid.uuid4())
    config = {
        "instance_id": instance_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "network_enabled": True,
        "last_sync_at": None,
        "last_sync_cursor": None,
        "domains_contributed": [],
    }
    _save_yaml(config_path, config)
    logger.info("Federation: created instance %s", instance_id[:8])
    return instance_id


def get_instance_config(able_home: Optional[Path] = None) -> Dict[str, Any]:
    """Return the full instance config, or empty dict if not enrolled."""
    home = able_home or _DEFAULT_ABLE_HOME
    config_path = home / _INSTANCE_FILE
    return _load_yaml(config_path)


def ensure_network_enrollment(able_home: Optional[Path] = None) -> Dict[str, Any]:
    """Idempotent: create instance.yaml with network_enabled=True if needed.

    If it exists but network_enabled is False (user opted out), respect that.
    Returns the instance config dict.
    """
    home = able_home or _DEFAULT_ABLE_HOME
    config_path = home / _INSTANCE_FILE
    config = _load_yaml(config_path)

    if config.get("instance_id"):
        # Already enrolled — don't override opt-out
        return config

    # First enrollment
    get_or_create_instance_id(home)
    return _load_yaml(config_path)


def set_network_enabled(enabled: bool, able_home: Optional[Path] = None) -> None:
    """Toggle network participation on or off."""
    home = able_home or _DEFAULT_ABLE_HOME
    config_path = home / _INSTANCE_FILE
    config = _load_yaml(config_path)

    if not config.get("instance_id"):
        get_or_create_instance_id(home)
        config = _load_yaml(config_path)

    config["network_enabled"] = enabled
    _save_yaml(config_path, config)
    logger.info("Federation: network_enabled set to %s", enabled)


def update_sync_cursor(
    cursor: str,
    domains: list[str] | None = None,
    able_home: Optional[Path] = None,
) -> None:
    """Update the last sync cursor and contributed domains."""
    home = able_home or _DEFAULT_ABLE_HOME
    config_path = home / _INSTANCE_FILE
    config = _load_yaml(config_path)
    if not config.get("instance_id"):
        return

    config["last_sync_at"] = datetime.now(timezone.utc).isoformat()
    config["last_sync_cursor"] = cursor
    if domains:
        existing = set(config.get("domains_contributed") or [])
        existing.update(domains)
        config["domains_contributed"] = sorted(existing)

    _save_yaml(config_path, config)
