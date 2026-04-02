"""Tests for repo package boundaries and hygiene."""

from __future__ import annotations

import importlib
import re
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = (
    REPO_ROOT / "able",
    REPO_ROOT / "able-studio" / "app",
    REPO_ROOT / "able" / "core" / "distillation" / "prompt_bank_data",
)
BARE_IMPORT_RE = re.compile(
    r"^\s*(from|import)\s+(core|tools|memory|clients|scheduler|billing|channels)(?:\.|\b)",
    re.MULTILINE,
)


def test_able_package_imports():
    pkg = importlib.import_module("able")
    assert pkg.__version__


def test_legacy_core_shim_is_removed():
    proc = subprocess.run(
        [sys.executable, "-c", "import importlib; importlib.import_module('core.distillation.models')"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "ModuleNotFoundError" in (proc.stderr or proc.stdout)


def test_no_duplicate_source_dirs_with_space_suffix():
    offenders: list[str] = []
    for root in SOURCE_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_dir() and path.name.endswith(" 2"):
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], f"Duplicate source directories found: {offenders}"


def test_no_bare_legacy_imports_in_python_sources():
    offenders: list[str] = []
    for path in (REPO_ROOT / "able").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in BARE_IMPORT_RE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{line}:{match.group(0).strip()}")
    assert offenders == [], "Bare legacy imports remain:\n" + "\n".join(offenders)
