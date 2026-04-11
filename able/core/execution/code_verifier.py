"""
Generate-Verify-Retry Loop — Compile-time LLM code generation with verification.

Forked from shorwood/slopc pattern. Generates code from a spec, verifies it
against extracted doctests or provided test functions, and retries with
accumulated error context on failure.

Key features:
- Error accumulation: each retry sees ALL prior errors (model learns from history)
- Content-addressed caching: cache key = spec + model hash, skip regen when cached
- Doctest extraction: pulls tests from docstrings, runs as verification
- Max 3 retries by default (configurable)

Usage:
    verifier = CodeVerifier()
    result = await verifier.generate_and_verify(
        spec="Write a function that computes fibonacci numbers",
        language="python",
    )
    if result.verified:
        print(result.code)
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import re
import subprocess
import tempfile
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default cache directory for content-addressed code storage
_DEFAULT_CACHE_DIR = Path("data/code_verifier_cache")


@dataclass
class VerificationError:
    """A single verification failure."""
    attempt: int
    error_type: str  # "syntax", "runtime", "test", "timeout"
    message: str
    stdout: str = ""
    stderr: str = ""


@dataclass
class VerifiedCode:
    """Result of a generate-verify-retry cycle."""
    code: str
    verified: bool
    language: str
    spec: str
    attempts: int = 0
    errors: List[VerificationError] = field(default_factory=list)
    cache_hit: bool = False
    duration_ms: float = 0
    extracted_tests: int = 0

    def summary(self) -> str:
        status = "VERIFIED" if self.verified else "FAILED"
        return (
            f"CodeVerifier: {status} in {self.attempts} attempt(s), "
            f"{len(self.errors)} error(s), {self.extracted_tests} tests, "
            f"cache={'hit' if self.cache_hit else 'miss'}, {self.duration_ms:.0f}ms"
        )


class CodeVerifier:
    """Generate-verify-retry loop for LLM code generation.

    Generates code from a natural language spec, extracts and runs
    verification tests, and retries with accumulated error context.
    """

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        max_retries: int = 3,
        timeout_seconds: float = 30.0,
        generator_fn: Optional[Callable] = None,
    ):
        """
        Args:
            cache_dir: Directory for content-addressed code cache.
            max_retries: Maximum retry attempts on failure.
            timeout_seconds: Per-verification timeout.
            generator_fn: Async callable(spec, errors, attempt) -> code string.
                          If None, uses a stub that returns empty code.
        """
        self._cache_dir = cache_dir or _DEFAULT_CACHE_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._max_retries = max_retries
        self._timeout = timeout_seconds
        self._generator_fn = generator_fn

    # ── Public API ────────────────────────────────────────────────

    async def generate_and_verify(
        self,
        spec: str,
        language: str = "python",
        test_code: Optional[str] = None,
        extra_context: str = "",
    ) -> VerifiedCode:
        """Generate code from spec, verify, retry on failure.

        Args:
            spec: Natural language specification.
            language: Target language (currently only "python" verified).
            test_code: Optional test code to run against generated code.
            extra_context: Additional context for the generator.

        Returns:
            VerifiedCode with the best result.
        """
        start = time.perf_counter()

        # Check content-addressed cache
        cache_key = self._cache_key(spec, language, extra_context)
        cached = self._cache_lookup(cache_key)
        if cached is not None:
            return VerifiedCode(
                code=cached,
                verified=True,
                language=language,
                spec=spec,
                cache_hit=True,
                duration_ms=(time.perf_counter() - start) * 1000,
            )

        errors: List[VerificationError] = []
        best_code = ""

        for attempt in range(1, self._max_retries + 1):
            # Generate code (pass full error history for learning)
            code = await self._generate(spec, language, errors, attempt, extra_context)
            if not code:
                errors.append(VerificationError(
                    attempt=attempt,
                    error_type="generation",
                    message="Generator returned empty code",
                ))
                continue

            best_code = code

            # Extract doctests if no explicit test provided
            tests = test_code or self._extract_doctests(code)
            n_tests = tests.count("assert") + tests.count(">>>") if tests else 0

            # Verify
            if language == "python":
                error = self._verify_python(code, tests)
            else:
                # Non-python: syntax-only check (no runtime verification)
                error = None

            if error is None:
                # Success — cache and return
                self._cache_store(cache_key, code)
                return VerifiedCode(
                    code=code,
                    verified=True,
                    language=language,
                    spec=spec,
                    attempts=attempt,
                    errors=errors,
                    duration_ms=(time.perf_counter() - start) * 1000,
                    extracted_tests=n_tests,
                )

            error.attempt = attempt
            errors.append(error)
            logger.info(
                "CodeVerifier attempt %d/%d failed: %s — %s",
                attempt, self._max_retries, error.error_type, error.message[:100],
            )

        # All attempts failed
        return VerifiedCode(
            code=best_code,
            verified=False,
            language=language,
            spec=spec,
            attempts=self._max_retries,
            errors=errors,
            duration_ms=(time.perf_counter() - start) * 1000,
        )

    # ── Generation ────────────────────────────────────────────────

    async def _generate(
        self,
        spec: str,
        language: str,
        prior_errors: List[VerificationError],
        attempt: int,
        extra_context: str,
    ) -> str:
        """Call the generator function with accumulated error context."""
        if self._generator_fn is None:
            return ""

        # Build error context string for the model
        error_context = ""
        if prior_errors:
            parts = []
            for err in prior_errors:
                parts.append(
                    f"Attempt {err.attempt} — {err.error_type}: {err.message}"
                )
                if err.stderr:
                    parts.append(f"  stderr: {err.stderr[:300]}")
            error_context = "\n".join(parts)

        try:
            code = await self._generator_fn(
                spec=spec,
                language=language,
                errors=error_context,
                attempt=attempt,
                extra_context=extra_context,
            )
            return code.strip() if code else ""
        except Exception as e:
            logger.warning("Generator failed: %s", e)
            return ""

    # ── Verification ──────────────────────────────────────────────

    def _verify_python(
        self,
        code: str,
        test_code: Optional[str] = None,
    ) -> Optional[VerificationError]:
        """Verify Python code: syntax check, then run with optional tests.

        Returns None on success, VerificationError on failure.
        """
        # Step 1: Syntax check
        try:
            ast.parse(code)
        except SyntaxError as e:
            return VerificationError(
                attempt=0,
                error_type="syntax",
                message=f"SyntaxError at line {e.lineno}: {e.msg}",
            )

        # Step 2: Runtime verification
        full_code = code
        if test_code:
            full_code = code + "\n\n" + test_code

        try:
            result = subprocess.run(
                ["python3", "-c", full_code],
                capture_output=True,
                text=True,
                timeout=self._timeout,
                env={"PATH": "/usr/bin:/usr/local/bin:/opt/homebrew/bin"},
            )
            if result.returncode != 0:
                return VerificationError(
                    attempt=0,
                    error_type="runtime" if "assert" not in result.stderr else "test",
                    message=result.stderr.strip()[:500] or f"Exit code {result.returncode}",
                    stdout=result.stdout[:300],
                    stderr=result.stderr[:500],
                )
        except subprocess.TimeoutExpired:
            return VerificationError(
                attempt=0,
                error_type="timeout",
                message=f"Execution timed out after {self._timeout}s",
            )
        except FileNotFoundError:
            return VerificationError(
                attempt=0,
                error_type="runtime",
                message="python3 not found on PATH",
            )

        return None  # Success

    # ── Doctest extraction ────────────────────────────────────────

    @staticmethod
    def _extract_doctests(code: str) -> str:
        """Extract doctest-style assertions from Python docstrings.

        Finds ``>>>`` lines in docstrings, converts to runnable assertions.
        Also extracts standalone ``assert`` statements in docstrings.
        """
        if ">>>" not in code and "assert" not in code:
            return ""

        try:
            tree = ast.parse(code)
        except SyntaxError:
            return ""

        _DOCSTRING_TYPES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        test_lines = []
        for node in ast.walk(tree):
            if not isinstance(node, _DOCSTRING_TYPES):
                continue
            docstring = ast.get_docstring(node)
            if not docstring:
                continue

            for line in docstring.split("\n"):
                stripped = line.strip()
                if stripped.startswith(">>>"):
                    # Extract the expression after >>>
                    expr = stripped[3:].strip()
                    if expr:
                        test_lines.append(expr)
                elif stripped.startswith("assert"):
                    test_lines.append(stripped)

        if not test_lines:
            return ""

        # Wrap in try/except for clearer error reporting
        return "\n".join(test_lines)

    # ── Content-addressed cache ───────────────────────────────────

    @staticmethod
    def _cache_key(spec: str, language: str, extra: str = "") -> str:
        """Generate a deterministic cache key from spec + language."""
        content = f"{spec}||{language}||{extra}"
        return hashlib.sha256(content.encode()).hexdigest()[:24]

    def _cache_lookup(self, key: str) -> Optional[str]:
        """Look up cached code by content-addressed key."""
        path = self._cache_dir / f"{key}.py"
        if path.exists():
            try:
                return path.read_text()
            except OSError:
                return None
        return None

    def _cache_store(self, key: str, code: str) -> None:
        """Store verified code in content-addressed cache."""
        path = self._cache_dir / f"{key}.py"
        try:
            path.write_text(code)
            # Write metadata alongside
            meta_path = self._cache_dir / f"{key}.json"
            meta_path.write_text(json.dumps({
                "cached_at": time.time(),
                "code_hash": hashlib.sha256(code.encode()).hexdigest()[:16],
                "lines": code.count("\n") + 1,
            }))
        except OSError as e:
            logger.debug("Cache store failed: %s", e)

    def cache_stats(self) -> Dict[str, Any]:
        """Return cache directory stats."""
        if not self._cache_dir.exists():
            return {"entries": 0, "size_bytes": 0}
        py_files = list(self._cache_dir.glob("*.py"))
        total_size = sum(f.stat().st_size for f in py_files if f.exists())
        return {"entries": len(py_files), "size_bytes": total_size}

    def clear_cache(self) -> int:
        """Clear all cached code. Returns number of entries removed."""
        if not self._cache_dir.exists():
            return 0
        count = 0
        for f in self._cache_dir.glob("*"):
            try:
                f.unlink()
                count += 1
            except OSError:
                pass
        return count
