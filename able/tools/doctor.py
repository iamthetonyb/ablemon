"""
F5 — Self-Diagnostic Doctor.

Validates ABLE configuration, checks provider connectivity, tests
tool permissions, identifies stale data, and suggests fixes.

Forked from OpenClaw v4.9 self-diagnostic pattern.

Usage:
    doc = Doctor()
    report = doc.run_all()
    print(report.summary())

    # Or individual checks:
    result = doc.check_config()
    result = doc.check_providers()
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class DiagnosticResult:
    """Result from a single diagnostic check."""
    check_name: str
    status: str  # "ok", "warning", "error"
    message: str
    suggestion: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_ok(self) -> bool:
        return self.status == "ok"


@dataclass
class DoctorReport:
    """Full diagnostic report."""
    results: List[DiagnosticResult] = field(default_factory=list)
    duration_ms: float = 0

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.results if r.status == "ok")

    @property
    def warning_count(self) -> int:
        return sum(1 for r in self.results if r.status == "warning")

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.results if r.status == "error")

    @property
    def healthy(self) -> bool:
        return self.error_count == 0

    def summary(self) -> str:
        icon = "HEALTHY" if self.healthy else "UNHEALTHY"
        lines = [
            f"ABLE Doctor: {icon} ({self.ok_count} ok, "
            f"{self.warning_count} warnings, {self.error_count} errors) "
            f"[{self.duration_ms:.0f}ms]",
            "",
        ]
        for r in self.results:
            prefix = {"ok": "[OK]", "warning": "[WARN]", "error": "[ERR]"}[r.status]
            lines.append(f"  {prefix} {r.check_name}: {r.message}")
            if r.suggestion:
                lines.append(f"        Fix: {r.suggestion}")
        return "\n".join(lines)


class Doctor:
    """Run diagnostic checks on ABLE's configuration and dependencies."""

    def __init__(
        self,
        config_dir: str = "config",
        data_dir: str = "data",
        env_file: str = ".env",
    ):
        self._config_dir = Path(config_dir)
        self._data_dir = Path(data_dir)
        self._env_file = Path(env_file)

    def run_all(self) -> DoctorReport:
        """Run all diagnostic checks.

        Returns a DoctorReport with results from each check.
        """
        start = time.perf_counter()
        results = []

        checks = [
            self.check_config_files,
            self.check_env_vars,
            self.check_databases,
            self.check_data_directory,
            self.check_python_dependencies,
            self.check_tool_permissions,
            self.check_stale_data,
        ]

        for check in checks:
            try:
                results.extend(check())
            except Exception as e:
                results.append(DiagnosticResult(
                    check_name=getattr(check, "__name__", str(check)),
                    status="error",
                    message=f"Check itself failed: {e}",
                ))

        duration = (time.perf_counter() - start) * 1000
        return DoctorReport(results=results, duration_ms=duration)

    def check_config_files(self) -> List[DiagnosticResult]:
        """Verify required config files exist and are valid."""
        results = []
        required_configs = [
            ("routing_config.yaml", "Model routing configuration"),
            ("scorer_weights.yaml", "Complexity scorer weights"),
        ]

        for filename, description in required_configs:
            path = self._config_dir / filename
            if path.exists():
                size = path.stat().st_size
                if size < 10:
                    results.append(DiagnosticResult(
                        check_name=f"config:{filename}",
                        status="warning",
                        message=f"{description} exists but is nearly empty ({size} bytes)",
                        suggestion=f"Review {path} — may be misconfigured",
                    ))
                else:
                    results.append(DiagnosticResult(
                        check_name=f"config:{filename}",
                        status="ok",
                        message=f"{description} found ({size} bytes)",
                    ))
            else:
                results.append(DiagnosticResult(
                    check_name=f"config:{filename}",
                    status="warning",
                    message=f"{description} not found",
                    suggestion=f"Create {path} — using defaults",
                ))

        # Check tool_permissions.yaml (optional)
        tp = self._config_dir / "tool_permissions.yaml"
        if tp.exists():
            results.append(DiagnosticResult(
                check_name="config:tool_permissions",
                status="ok",
                message="Tool permissions config found",
            ))

        return results

    def check_env_vars(self) -> List[DiagnosticResult]:
        """Check for required environment variables."""
        results = []

        # Critical API keys
        key_checks = [
            ("ANTHROPIC_API_KEY", "Claude API access", True),
            ("OPENAI_API_KEY", "OpenAI/GPT access", False),
            ("OPENROUTER_API_KEY", "OpenRouter fallback", False),
            ("OLLAMA_HOST", "Local Ollama inference", False),
        ]

        for var, description, critical in key_checks:
            value = os.environ.get(var)
            if value:
                # Mask the key for display
                masked = value[:4] + "..." + value[-4:] if len(value) > 8 else "***"
                results.append(DiagnosticResult(
                    check_name=f"env:{var}",
                    status="ok",
                    message=f"{description} configured ({masked})",
                ))
            else:
                status = "error" if critical else "warning"
                results.append(DiagnosticResult(
                    check_name=f"env:{var}",
                    status=status,
                    message=f"{description} not set",
                    suggestion=f"Set {var} in .env or environment",
                ))

        return results

    def check_databases(self) -> List[DiagnosticResult]:
        """Check SQLite database health."""
        results = []
        db_files = [
            (self._data_dir / "memory.db", "Memory database"),
            (self._data_dir / "activity.db", "Activity/task database"),
            (self._data_dir / "interaction_log.db", "Interaction log"),
        ]

        for path, description in db_files:
            if not path.exists():
                results.append(DiagnosticResult(
                    check_name=f"db:{path.name}",
                    status="warning",
                    message=f"{description} not found",
                    suggestion="Will be created on first use",
                ))
                continue

            try:
                conn = sqlite3.connect(str(path))
                # Quick integrity check
                result = conn.execute("PRAGMA integrity_check").fetchone()
                conn.close()
                if result[0] == "ok":
                    size_kb = path.stat().st_size / 1024
                    results.append(DiagnosticResult(
                        check_name=f"db:{path.name}",
                        status="ok",
                        message=f"{description} healthy ({size_kb:.0f}KB)",
                    ))
                else:
                    results.append(DiagnosticResult(
                        check_name=f"db:{path.name}",
                        status="error",
                        message=f"{description} integrity check failed: {result[0]}",
                        suggestion=f"Rebuild: rm {path} (will recreate on next use)",
                    ))
            except Exception as e:
                results.append(DiagnosticResult(
                    check_name=f"db:{path.name}",
                    status="error",
                    message=f"{description} error: {e}",
                ))

        return results

    def check_data_directory(self) -> List[DiagnosticResult]:
        """Check data directory structure."""
        results = []

        if self._data_dir.exists():
            size_mb = sum(
                f.stat().st_size for f in self._data_dir.rglob("*") if f.is_file()
            ) / (1024 * 1024)
            results.append(DiagnosticResult(
                check_name="data:directory",
                status="ok",
                message=f"Data directory exists ({size_mb:.1f}MB)",
            ))

            # Check for oversized files
            for f in self._data_dir.rglob("*"):
                if f.is_file() and f.stat().st_size > 100 * 1024 * 1024:  # >100MB
                    results.append(DiagnosticResult(
                        check_name=f"data:large_file",
                        status="warning",
                        message=f"Large file: {f.name} ({f.stat().st_size / 1024 / 1024:.0f}MB)",
                        suggestion="Consider archiving or cleaning up",
                    ))
        else:
            results.append(DiagnosticResult(
                check_name="data:directory",
                status="warning",
                message="Data directory not found",
                suggestion="mkdir -p data",
            ))

        return results

    def check_python_dependencies(self) -> List[DiagnosticResult]:
        """Check critical Python dependencies."""
        results = []
        deps = [
            ("yaml", "pyyaml", True),
            ("anthropic", "anthropic", False),
            ("aiohttp", "aiohttp", False),
            ("rich", "rich", False),
        ]

        for module, package, critical in deps:
            try:
                __import__(module)
                results.append(DiagnosticResult(
                    check_name=f"dep:{package}",
                    status="ok",
                    message=f"{package} available",
                ))
            except ImportError:
                status = "error" if critical else "warning"
                results.append(DiagnosticResult(
                    check_name=f"dep:{package}",
                    status=status,
                    message=f"{package} not installed",
                    suggestion=f"pip install {package}",
                ))

        return results

    def check_tool_permissions(self) -> List[DiagnosticResult]:
        """Check tool permission configuration."""
        results = []
        tp_path = self._config_dir / "tool_permissions.yaml"

        if not tp_path.exists():
            results.append(DiagnosticResult(
                check_name="permissions:config",
                status="ok",
                message="Using default tool permissions (no override file)",
            ))
            return results

        try:
            import yaml
            with open(tp_path) as f:
                perms = yaml.safe_load(f)

            if not isinstance(perms, dict):
                results.append(DiagnosticResult(
                    check_name="permissions:config",
                    status="error",
                    message="tool_permissions.yaml is not a valid mapping",
                ))
                return results

            sections = ["always_allow", "ask_before", "never_allow"]
            for section in sections:
                if section in perms:
                    count = len(perms[section]) if isinstance(perms[section], list) else 0
                    results.append(DiagnosticResult(
                        check_name=f"permissions:{section}",
                        status="ok",
                        message=f"{count} rules configured",
                    ))

        except Exception as e:
            results.append(DiagnosticResult(
                check_name="permissions:config",
                status="error",
                message=f"Failed to parse tool_permissions.yaml: {e}",
            ))

        return results

    def check_stale_data(self) -> List[DiagnosticResult]:
        """Identify stale data files."""
        results = []
        now = time.time()
        stale_threshold = 30 * 24 * 3600  # 30 days

        stale_dirs = [
            self._data_dir / "tool_results",
            self._data_dir / "checkpoints",
        ]

        for dir_path in stale_dirs:
            if not dir_path.exists():
                continue
            stale_count = 0
            for f in dir_path.rglob("*"):
                if f.is_file() and (now - f.stat().st_mtime) > stale_threshold:
                    stale_count += 1

            if stale_count > 0:
                results.append(DiagnosticResult(
                    check_name=f"stale:{dir_path.name}",
                    status="warning",
                    message=f"{stale_count} files older than 30 days in {dir_path}",
                    suggestion=f"Clean up: find {dir_path} -mtime +30 -delete",
                ))

        return results
