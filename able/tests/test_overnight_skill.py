"""Tests for D11 — Overnight Orchestrator Skill.

Covers: skill execution, parameter handling, OvernightLoop integration,
report generation, error cases.
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from able.skills.library.overnight.implement import (
    run_overnight_skill,
    SKILL_METADATA,
)


# ── Skill metadata ───────────────────────────────────────────────

class TestSkillMetadata:

    def test_name(self):
        assert SKILL_METADATA["name"] == "overnight"

    def test_triggers(self):
        triggers = SKILL_METADATA["triggers"]
        assert "run overnight" in triggers
        assert "autonomous loop" in triggers

    def test_trust_level(self):
        assert SKILL_METADATA["trust_level"] == 3

    def test_category(self):
        assert SKILL_METADATA["category"] == "execution"


# ── Parameter handling ────────────────────────────────────────────

class TestParameterHandling:

    @pytest.mark.asyncio
    async def test_missing_task_returns_error(self):
        result = await run_overnight_skill({})
        assert result["success"] is False
        assert "No task" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_task_returns_error(self):
        result = await run_overnight_skill({"task": ""})
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_nonexistent_work_dir(self):
        result = await run_overnight_skill({
            "task": "test",
            "work_dir": "/nonexistent/path/xyz",
        })
        assert result["success"] is False
        assert "does not exist" in result["error"]


# ── Skill execution ──────────────────────────────────────────────

class TestSkillExecution:

    @pytest.mark.asyncio
    async def test_runs_with_default_params(self, tmp_path):
        """Skill should execute with minimal args."""
        mock_report = MagicMock()
        mock_report.run_id = "test-run-1"
        mock_report.iterations_total = 3
        mock_report.iterations_succeeded = 3
        mock_report.iterations_failed = 0
        mock_report.abort_reason = None

        with patch("able.core.execution.overnight_loop.OvernightLoop") as MockLoop:
            instance = MockLoop.return_value
            instance.run = AsyncMock(return_value=mock_report)
            instance._notes_path = tmp_path / "notes.md"

            result = await run_overnight_skill({
                "task": "test task",
                "work_dir": str(tmp_path),
                "max_iterations": 3,
            })

            assert result["success"] is True
            assert result["report"]["iterations_total"] == 3
            assert result["report"]["iterations_succeeded"] == 3
            assert result["report"]["abort_reason"] is None

    @pytest.mark.asyncio
    async def test_returns_notes_content(self, tmp_path):
        (tmp_path / "notes.md").write_text("# Learnings\n- step 1 worked")

        mock_report = MagicMock()
        mock_report.run_id = "run-2"
        mock_report.iterations_total = 1
        mock_report.iterations_succeeded = 1
        mock_report.iterations_failed = 0
        mock_report.abort_reason = None

        with patch("able.core.execution.overnight_loop.OvernightLoop") as MockLoop:
            instance = MockLoop.return_value
            instance.run = AsyncMock(return_value=mock_report)
            # Point _notes_path to non-existent so fallback to root notes.md
            instance._notes_path = tmp_path / "nonexistent" / "notes.md"

            result = await run_overnight_skill({
                "task": "test",
                "work_dir": str(tmp_path),
            })

            assert "step 1 worked" in result["notes"]

    @pytest.mark.asyncio
    async def test_abort_reported(self, tmp_path):
        mock_report = MagicMock()
        mock_report.run_id = "run-3"
        mock_report.iterations_total = 3
        mock_report.iterations_succeeded = 0
        mock_report.iterations_failed = 3
        mock_report.abort_reason = "3 consecutive failures"

        with patch("able.core.execution.overnight_loop.OvernightLoop") as MockLoop:
            instance = MockLoop.return_value
            instance.run = AsyncMock(return_value=mock_report)
            instance._notes_path = tmp_path / "notes.md"

            result = await run_overnight_skill({
                "task": "failing task",
                "work_dir": str(tmp_path),
            })

            assert result["success"] is False
            assert result["report"]["abort_reason"] == "3 consecutive failures"


# ── SKILL.md validation ──────────────────────────────────────────

class TestSkillFile:

    def test_skill_md_exists(self):
        skill_path = Path(__file__).parent.parent / "skills" / "library" / "overnight" / "SKILL.md"
        assert skill_path.exists()

    def test_skill_md_has_triggers(self):
        skill_path = Path(__file__).parent.parent / "skills" / "library" / "overnight" / "SKILL.md"
        content = skill_path.read_text()
        assert "run overnight" in content
        assert "autonomous loop" in content

    def test_implement_exists(self):
        impl_path = Path(__file__).parent.parent / "skills" / "library" / "overnight" / "implement.py"
        assert impl_path.exists()
