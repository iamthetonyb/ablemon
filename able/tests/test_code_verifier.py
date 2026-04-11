"""Tests for D4 — Generate-Verify-Retry Loop.

Covers: syntax verification, runtime verification, doctest extraction,
content-addressed caching, error accumulation, retry logic, cache stats.
"""

import pytest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from able.core.execution.code_verifier import (
    CodeVerifier,
    VerifiedCode,
    VerificationError,
)


@pytest.fixture
def verifier(tmp_path):
    return CodeVerifier(cache_dir=tmp_path / "cache")


@pytest.fixture
def verifier_with_generator(tmp_path):
    """Verifier with a mock generator that returns valid Python."""
    async def _gen(spec, language, errors, attempt, extra_context):
        if errors and attempt > 1:
            # "Learn" from errors — return fixed code on retry
            return "def add(a, b):\n    return a + b\nassert add(2, 3) == 5"
        return "def add(a, b):\n    return a + b\nassert add(2, 3) == 5"

    return CodeVerifier(
        cache_dir=tmp_path / "cache",
        generator_fn=_gen,
    )


# ── Python verification ──────────────────────────────────────────

class TestPythonVerification:

    def test_valid_code_passes(self, verifier):
        error = verifier._verify_python("x = 1 + 1\nassert x == 2")
        assert error is None

    def test_syntax_error_detected(self, verifier):
        error = verifier._verify_python("def bad(:\n    pass")
        assert error is not None
        assert error.error_type == "syntax"
        assert "SyntaxError" in error.message

    def test_runtime_error_detected(self, verifier):
        error = verifier._verify_python("x = 1 / 0")
        assert error is not None
        assert error.error_type == "runtime"
        assert "ZeroDivision" in error.message or "division" in error.stderr.lower()

    def test_assertion_failure_detected(self, verifier):
        error = verifier._verify_python("assert 1 == 2, 'math broke'")
        assert error is not None
        # May be classified as "test" or "runtime" depending on stderr content
        assert error.error_type in ("test", "runtime")
        assert "assert" in error.message.lower() or "assert" in error.stderr.lower()

    def test_with_explicit_test_code(self, verifier):
        code = "def square(n): return n * n"
        test = "assert square(3) == 9\nassert square(0) == 0"
        error = verifier._verify_python(code, test)
        assert error is None

    def test_test_failure_with_test_code(self, verifier):
        code = "def square(n): return n + n"  # Bug: add instead of multiply
        test = "assert square(3) == 9"
        error = verifier._verify_python(code, test)
        assert error is not None

    def test_timeout_detected(self):
        verifier = CodeVerifier(timeout_seconds=1.0)
        error = verifier._verify_python("import time; time.sleep(10)")
        assert error is not None
        assert error.error_type == "timeout"

    def test_empty_code_valid(self, verifier):
        error = verifier._verify_python("")
        assert error is None  # Empty Python is valid


# ── Doctest extraction ────────────────────────────────────────────

class TestDoctestExtraction:

    def test_extract_doctest_examples(self):
        code = '''
def add(a, b):
    """Add two numbers.

    >>> add(2, 3)
    5
    >>> add(0, 0)
    0
    """
    return a + b
'''
        tests = CodeVerifier._extract_doctests(code)
        assert "add(2, 3)" in tests
        assert "add(0, 0)" in tests

    def test_extract_assert_from_docstring(self):
        code = '''
def fib(n):
    """Fibonacci.

    assert fib(0) == 0
    assert fib(1) == 1
    assert fib(10) == 55
    """
    if n <= 1:
        return n
    return fib(n-1) + fib(n-2)
'''
        tests = CodeVerifier._extract_doctests(code)
        assert "assert fib(0) == 0" in tests
        assert "assert fib(10) == 55" in tests

    def test_no_doctests(self):
        code = "x = 42\nprint(x)"
        tests = CodeVerifier._extract_doctests(code)
        assert tests == ""

    def test_syntax_error_code(self):
        tests = CodeVerifier._extract_doctests("def bad(:\n  pass")
        assert tests == ""


# ── Content-addressed caching ─────────────────────────────────────

class TestCaching:

    def test_cache_key_deterministic(self):
        k1 = CodeVerifier._cache_key("spec", "python")
        k2 = CodeVerifier._cache_key("spec", "python")
        assert k1 == k2

    def test_cache_key_differs_by_spec(self):
        k1 = CodeVerifier._cache_key("spec A", "python")
        k2 = CodeVerifier._cache_key("spec B", "python")
        assert k1 != k2

    def test_cache_key_differs_by_language(self):
        k1 = CodeVerifier._cache_key("spec", "python")
        k2 = CodeVerifier._cache_key("spec", "javascript")
        assert k1 != k2

    def test_cache_store_and_lookup(self, verifier):
        key = "test_key_001"
        verifier._cache_store(key, "x = 42")
        result = verifier._cache_lookup(key)
        assert result == "x = 42"

    def test_cache_miss(self, verifier):
        result = verifier._cache_lookup("nonexistent_key")
        assert result is None

    def test_cache_stats(self, verifier):
        verifier._cache_store("k1", "code1")
        verifier._cache_store("k2", "code2")
        stats = verifier.cache_stats()
        assert stats["entries"] == 2
        assert stats["size_bytes"] > 0

    def test_clear_cache(self, verifier):
        verifier._cache_store("k1", "code1")
        verifier._cache_store("k2", "code2")
        removed = verifier.clear_cache()
        assert removed >= 2  # .py + .json files
        assert verifier.cache_stats()["entries"] == 0

    @pytest.mark.asyncio
    async def test_cache_hit_skips_generation(self, verifier):
        """When code is cached, generator should not be called."""
        key = verifier._cache_key("cached spec", "python")
        verifier._cache_store(key, "cached_code = True")

        result = await verifier.generate_and_verify(
            spec="cached spec", language="python"
        )
        assert result.cache_hit is True
        assert result.verified is True
        assert "cached_code" in result.code


# ── Generate-verify-retry cycle ───────────────────────────────────

class TestGenerateVerifyRetry:

    @pytest.mark.asyncio
    async def test_successful_generation(self, verifier_with_generator):
        result = await verifier_with_generator.generate_and_verify(
            spec="add function",
        )
        assert result.verified is True
        assert result.attempts == 1
        assert len(result.errors) == 0
        assert result.duration_ms > 0

    @pytest.mark.asyncio
    async def test_no_generator_all_fail(self, verifier):
        """Without a generator, all attempts produce empty code."""
        result = await verifier.generate_and_verify(spec="anything")
        assert result.verified is False
        assert result.attempts == 3  # default max_retries
        assert len(result.errors) == 3
        assert all(e.error_type == "generation" for e in result.errors)

    @pytest.mark.asyncio
    async def test_retry_with_error_accumulation(self, tmp_path):
        """Generator receives all prior errors on retry."""
        received_errors = []

        async def _gen(spec, language, errors, attempt, extra_context):
            received_errors.append(errors)
            if attempt == 1:
                return "x = 1 / 0"  # Will fail
            return "x = 42\nassert x == 42"  # Fixed on retry

        v = CodeVerifier(cache_dir=tmp_path / "cache", generator_fn=_gen)
        result = await v.generate_and_verify(spec="test")

        assert result.verified is True
        assert result.attempts == 2
        # Second call should have received first error
        assert "ZeroDivision" in received_errors[1] or "division" in received_errors[1].lower()

    @pytest.mark.asyncio
    async def test_all_retries_fail(self, tmp_path):
        async def _bad_gen(spec, language, errors, attempt, extra_context):
            return "x = 1 / 0"  # Always fails

        v = CodeVerifier(cache_dir=tmp_path / "cache", max_retries=2, generator_fn=_bad_gen)
        result = await v.generate_and_verify(spec="bad")

        assert result.verified is False
        assert result.attempts == 2
        assert len(result.errors) == 2

    @pytest.mark.asyncio
    async def test_successful_code_cached(self, verifier_with_generator):
        result = await verifier_with_generator.generate_and_verify(spec="add test")
        assert result.verified is True
        assert result.cache_hit is False

        # Second call should hit cache
        result2 = await verifier_with_generator.generate_and_verify(spec="add test")
        assert result2.cache_hit is True


# ── VerifiedCode dataclass ────────────────────────────────────────

class TestVerifiedCode:

    def test_summary_verified(self):
        vc = VerifiedCode(
            code="x = 1", verified=True, language="python",
            spec="test", attempts=1, duration_ms=42.5,
        )
        s = vc.summary()
        assert "VERIFIED" in s
        assert "1 attempt" in s

    def test_summary_failed(self):
        vc = VerifiedCode(
            code="x = 1", verified=False, language="python",
            spec="test", attempts=3, duration_ms=100,
            errors=[
                VerificationError(attempt=1, error_type="syntax", message="bad"),
                VerificationError(attempt=2, error_type="runtime", message="crash"),
            ],
        )
        s = vc.summary()
        assert "FAILED" in s
        assert "2 error" in s
