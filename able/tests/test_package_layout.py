"""Tests for package/import stability after the ABLE rename."""

from __future__ import annotations

import importlib


def test_able_package_imports():
    pkg = importlib.import_module("able")
    assert pkg.__version__


def test_legacy_core_shim_resolves_to_able_core():
    models = importlib.import_module("core.distillation.models")
    assert hasattr(models, "TrainingPair")
