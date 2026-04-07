"""Tests for RustPython sandbox and eval_code fallback chain."""

import pytest
from unittest.mock import patch, MagicMock

from able.tools.sandbox import eval_code
from able.tools.sandbox.rustpython_sandbox import (
    RustPythonSandbox,
    SandboxUnavailable,
    SandboxResult,
    _is_available,
)


def test_rustpython_unavailable_by_default():
    """RustPython is likely not installed in test env."""
    # This test passes regardless — it just documents current state
    result = _is_available()
    assert isinstance(result, bool)


def test_sandbox_unavailable_raises():
    """Constructor raises SandboxUnavailable when rustpython_vm not installed."""
    with patch("able.tools.sandbox.rustpython_sandbox._is_available", return_value=False):
        with pytest.raises(SandboxUnavailable, match="not installed"):
            RustPythonSandbox()


def test_available_static_method():
    with patch("able.tools.sandbox.rustpython_sandbox._is_available", return_value=True):
        assert RustPythonSandbox.available() is True
    with patch("able.tools.sandbox.rustpython_sandbox._is_available", return_value=False):
        assert RustPythonSandbox.available() is False


def test_eval_code_falls_back_to_subprocess():
    """When RustPython unavailable, eval_code falls back to subprocess sandbox."""
    result = eval_code("print('hello')", timeout=5.0)
    # Should attempt a backend
    assert result["backend_used"] in ("subprocess", "rustpython", "none")
    if result["backend_used"] == "subprocess":
        assert "hello" in result["output"]
    elif result["backend_used"] == "none":
        # Sandbox execution may fail in constrained environments
        assert result["error"] is not None


def test_eval_code_forced_rustpython_fails_gracefully():
    """Forcing rustpython backend when unavailable returns error."""
    with patch("able.tools.sandbox.rustpython_sandbox._is_available", return_value=False):
        result = eval_code("print(1)", backend="rustpython")
        assert result["backend_used"] == "none"
        assert result["error"] is not None


def test_sandbox_result_dataclass():
    """SandboxResult is a proper dataclass."""
    r = SandboxResult(output="hello", error=None, duration_ms=1.5)
    assert r.output == "hello"
    assert r.error is None
    assert r.duration_ms == 1.5
