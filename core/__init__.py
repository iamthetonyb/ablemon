"""Compatibility shim for legacy ``core.*`` imports.

This allows older modules to resolve through the canonical ``able.core``
package while the codebase is migrated to fully-qualified imports.
"""

from __future__ import annotations

from pathlib import Path

__path__ = [str(Path(__file__).resolve().parent.parent / "able" / "core")]
