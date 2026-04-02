"""Compatibility shim for legacy ``tools.*`` imports."""

from __future__ import annotations

from pathlib import Path

__path__ = [str(Path(__file__).resolve().parent.parent / "able" / "tools")]
