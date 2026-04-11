"""Tests for F5 — Self-Diagnostic Doctor.

Covers: config checks, env var checks, DB checks, dependency checks,
report structure, summary formatting.
"""

import os
import sqlite3
import pytest
from unittest.mock import patch

from able.tools.doctor import (
    DiagnosticResult,
    Doctor,
    DoctorReport,
)


@pytest.fixture
def doctor(tmp_path):
    """Doctor with isolated directories."""
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir()
    data_dir.mkdir()
    return Doctor(
        config_dir=str(config_dir),
        data_dir=str(data_dir),
    )


# ── DiagnosticResult ─────────────────────────────────────────────

class TestDiagnosticResult:

    def test_ok_result(self):
        r = DiagnosticResult(check_name="test", status="ok", message="all good")
        assert r.is_ok

    def test_error_result(self):
        r = DiagnosticResult(check_name="test", status="error", message="broken")
        assert not r.is_ok

    def test_with_suggestion(self):
        r = DiagnosticResult(
            check_name="test", status="warning",
            message="issue", suggestion="fix it",
        )
        assert r.suggestion == "fix it"


# ── Config checks ────────────────────────────────────────────────

class TestConfigChecks:

    def test_missing_configs(self, doctor):
        results = doctor.check_config_files()
        warnings = [r for r in results if r.status == "warning"]
        assert len(warnings) >= 1  # At least routing_config missing

    def test_present_configs(self, doctor, tmp_path):
        config_dir = tmp_path / "config"
        (config_dir / "routing_config.yaml").write_text("providers:\n  - name: test\n" * 5)
        (config_dir / "scorer_weights.yaml").write_text("weights:\n  complexity: 0.5\n" * 5)
        results = doctor.check_config_files()
        ok_results = [r for r in results if r.status == "ok"]
        assert len(ok_results) >= 2

    def test_empty_config_warns(self, doctor, tmp_path):
        config_dir = tmp_path / "config"
        (config_dir / "routing_config.yaml").write_text("")
        results = doctor.check_config_files()
        warnings = [r for r in results if r.status == "warning"]
        assert any("empty" in r.message for r in warnings)


# ── Env var checks ───────────────────────────────────────────────

class TestEnvVarChecks:

    def test_anthropic_key_present(self, doctor):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test-1234567890"}):
            results = doctor.check_env_vars()
            anthropic = [r for r in results if "ANTHROPIC" in r.check_name]
            assert anthropic[0].status == "ok"

    def test_anthropic_key_missing(self, doctor):
        env = os.environ.copy()
        env.pop("ANTHROPIC_API_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            results = doctor.check_env_vars()
            anthropic = [r for r in results if "ANTHROPIC" in r.check_name]
            assert anthropic[0].status == "error"

    def test_optional_key_warning(self, doctor):
        env = os.environ.copy()
        env.pop("OPENAI_API_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            results = doctor.check_env_vars()
            openai = [r for r in results if "OPENAI" in r.check_name]
            if openai:
                assert openai[0].status == "warning"  # Optional, not error


# ── Database checks ──────────────────────────────────────────────

class TestDatabaseChecks:

    def test_missing_db(self, doctor):
        results = doctor.check_databases()
        # All DBs should report "not found" → warning
        assert all(r.status == "warning" for r in results)

    def test_healthy_db(self, doctor, tmp_path):
        db_path = tmp_path / "data" / "memory.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.commit()
        conn.close()
        results = doctor.check_databases()
        memory_checks = [r for r in results if "memory" in r.check_name]
        assert any(r.status == "ok" for r in memory_checks)


# ── Dependency checks ────────────────────────────────────────────

class TestDependencyChecks:

    def test_yaml_available(self, doctor):
        results = doctor.check_python_dependencies()
        yaml_check = [r for r in results if "pyyaml" in r.check_name]
        assert yaml_check[0].status == "ok"

    def test_missing_dep(self, doctor):
        with patch("builtins.__import__", side_effect=ImportError("no module")):
            # This will fail for ALL imports, but doctor catches it
            results = doctor.check_python_dependencies()
            # At least some should be errors
            assert any(r.status in ("error", "warning") for r in results)


# ── Full report ──────────────────────────────────────────────────

class TestReport:

    def test_run_all(self, doctor):
        report = doctor.run_all()
        assert isinstance(report, DoctorReport)
        assert report.duration_ms > 0
        assert len(report.results) > 0

    def test_report_healthy(self):
        report = DoctorReport(results=[
            DiagnosticResult("a", "ok", "fine"),
            DiagnosticResult("b", "ok", "good"),
        ])
        assert report.healthy
        assert report.ok_count == 2

    def test_report_unhealthy(self):
        report = DoctorReport(results=[
            DiagnosticResult("a", "ok", "fine"),
            DiagnosticResult("b", "error", "broken"),
        ])
        assert not report.healthy
        assert report.error_count == 1

    def test_summary_format(self):
        report = DoctorReport(
            results=[
                DiagnosticResult("check1", "ok", "all good"),
                DiagnosticResult("check2", "warning", "hmm", suggestion="fix it"),
                DiagnosticResult("check3", "error", "broken"),
            ],
            duration_ms=42.0,
        )
        summary = report.summary()
        assert "UNHEALTHY" in summary
        assert "[OK]" in summary
        assert "[WARN]" in summary
        assert "[ERR]" in summary
        assert "Fix:" in summary

    def test_healthy_summary(self):
        report = DoctorReport(
            results=[DiagnosticResult("a", "ok", "fine")],
            duration_ms=5.0,
        )
        assert "HEALTHY" in report.summary()

    def test_check_failure_caught(self, doctor):
        """Doctor should survive individual check failures."""
        with patch.object(doctor, 'check_config_files', side_effect=RuntimeError("boom")):
            report = doctor.run_all()
            errors = [r for r in report.results if "failed" in r.message.lower()]
            assert len(errors) >= 1
