"""
Shared helper functions for tenant data access.

Avoids duplicating config loading, corpus counting, and adapter
detection across multiple tenant modules.
"""

from pathlib import Path
from typing import Any, Dict, Optional

import yaml


def load_tenant_config(
    config_dir: Path, tenant_id: str
) -> Optional[Dict[str, Any]]:
    """Load tenant config from YAML. Returns None if not found."""
    config_path = config_dir / f"{tenant_id}.yaml"
    if not config_path.exists():
        return None
    with open(config_path) as f:
        return yaml.safe_load(f)


def count_corpus_files(data_dir: Path, tenant_id: str) -> int:
    """Count files in a tenant's corpus directory."""
    corpus_dir = data_dir / tenant_id / "corpus"
    if not corpus_dir.exists():
        return 0
    return len(list(corpus_dir.glob("*")))


def has_adapter(data_dir: Path, tenant_id: str) -> bool:
    """Check if tenant has a trained LoRA adapter (.bin or .gguf)."""
    adapter_dir = data_dir / tenant_id / "adapters"
    if not adapter_dir.exists():
        return False
    return any(adapter_dir.glob("*.bin")) or any(adapter_dir.glob("*.gguf"))


def get_adapter_path(data_dir: Path, tenant_id: str) -> Optional[str]:
    """Get path to the latest adapter file. Prefers .gguf over .bin."""
    adapter_dir = data_dir / tenant_id / "adapters"
    if not adapter_dir.exists():
        return None

    for ext in ("*.gguf", "*.bin"):
        adapters = sorted(
            adapter_dir.glob(ext),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if adapters:
            return str(adapters[0])
    return None


def get_adapter_version(data_dir: Path, tenant_id: str) -> Optional[str]:
    """Get the latest adapter filename (for display)."""
    path = get_adapter_path(data_dir, tenant_id)
    if path:
        return Path(path).name
    return None
