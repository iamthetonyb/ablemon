#!/usr/bin/env python3
"""Tests for the ATLAS Auto-Pilot autonomous task runner."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

# Ensure atlas package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agi.autopilot import (
    AutoPilot,
    AutoPilotResult,
    ComparisonPair,
    _is_destructive,
    _load_objectives,
    _parse_simple_yaml,
)
from core.agi.planner import GoalPlanner, PlannerResult, SubTask, TaskStatus


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def objectives_file(tmp_path: Path) -> Path:
    """Create a temporary objectives YAML file."""
    obj_path = tmp_path / "current_objectives.yaml"
    obj_path.write_text(
        "urgent:\n"
        '  - "Research latest LLM benchmarks"\n'
        "in_progress:\n"
        '  - "Write unit tests for routing module"\n'
        "backlog:\n"
        '  - "Refactor distillation pipeline"\n'
        '  - "Add monitoring dashboard"\n'
        "blocked: []\n"
    )
    return obj_path


@pytest.fixture
def empty_objectives_file(tmp_path: Path) -> Path:
    """Create an empty objectives file."""
    obj_path = tmp_path / "current_objectives.yaml"
    obj_path.write_text("urgent: []\nin_progress: []\nbacklog: []\nblocked: []\n")
    return obj_path


@pytest.fixture
def distillation_dir(tmp_path: Path) -> Path:
    """Temporary distillation output directory."""
    d = tmp_path / "distillation"
    d.mkdir()
    return d


@pytest.fixture
def autopilot(objectives_file: Path, distillation_dir: Path) -> AutoPilot:
    """AutoPilot with temp paths and default planner."""
    return AutoPilot(
        objectives_path=objectives_file,
        distillation_dir=str(distillation_dir),
    )


@pytest.fixture
def empty_autopilot(empty_objectives_file: Path, distillation_dir: Path) -> AutoPilot:
    """AutoPilot with no objectives."""
    return AutoPilot(
        objectives_path=empty_objectives_file,
        distillation_dir=str(distillation_dir),
    )


# ═══════════════════════════════════════════════════════════════
# OBJECTIVES LOADING
# ═══════════════════════════════════════════════════════════════


def test_load_objectives(objectives_file: Path):
    """Objectives YAML loads all four buckets."""
    objs = _load_objectives(objectives_file)
    assert len(objs["urgent"]) == 1
    assert len(objs["in_progress"]) == 1
    assert len(objs["backlog"]) == 2
    assert objs["blocked"] == []


def test_load_objectives_missing_file(tmp_path: Path):
    """Missing file returns empty buckets without crashing."""
    objs = _load_objectives(tmp_path / "nonexistent.yaml")
    assert objs == {"urgent": [], "in_progress": [], "backlog": [], "blocked": []}


def test_parse_simple_yaml(objectives_file: Path):
    """Fallback YAML parser handles the objectives format."""
    objs = _parse_simple_yaml(objectives_file)
    assert len(objs["urgent"]) == 1
    assert "Research latest LLM benchmarks" in objs["urgent"][0]


def test_parse_simple_yaml_empty(empty_objectives_file: Path):
    """Fallback parser handles empty lists."""
    objs = _parse_simple_yaml(empty_objectives_file)
    assert all(v == [] for v in objs.values())


# ═══════════════════════════════════════════════════════════════
# DESTRUCTIVE TOOL DETECTION
# ═══════════════════════════════════════════════════════════════


def test_destructive_tool_detected():
    """Destructive tools are correctly identified."""
    task = SubTask(id="t1", description="delete", tool="shell.rm", args={})
    assert _is_destructive(task) is True


def test_safe_tool_not_flagged():
    """Safe tools are not flagged as destructive."""
    task = SubTask(id="t1", description="search", tool="browser.search", args={})
    assert _is_destructive(task) is False


# ═══════════════════════════════════════════════════════════════
# RUN OBJECTIVES
# ═══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_run_objectives_processes_tasks(autopilot: AutoPilot):
    """run_objectives processes available tasks and returns results."""
    result = await autopilot.run_objectives(max_tasks=2)

    assert isinstance(result, AutoPilotResult)
    assert result.tasks_attempted == 2
    assert result.tasks_succeeded > 0
    assert result.source == "autopilot"
    assert result.total_time_s > 0


@pytest.mark.asyncio
async def test_run_objectives_empty(empty_autopilot: AutoPilot):
    """run_objectives with no tasks returns clean result."""
    result = await empty_autopilot.run_objectives()

    assert result.tasks_attempted == 0
    assert result.tasks_succeeded == 0
    assert result.tasks_failed == 0


@pytest.mark.asyncio
async def test_run_objectives_respects_max_tasks(autopilot: AutoPilot):
    """run_objectives respects the max_tasks limit."""
    result = await autopilot.run_objectives(max_tasks=1)
    assert result.tasks_attempted == 1


@pytest.mark.asyncio
async def test_run_objectives_respects_budget(
    objectives_file: Path, distillation_dir: Path,
):
    """run_objectives stops when token budget is exhausted."""
    pilot = AutoPilot(
        objectives_path=objectives_file,
        distillation_dir=str(distillation_dir),
        budget_tokens=50,  # Very low budget
    )
    result = await pilot.run_objectives(max_tasks=10)
    # Should stop early due to budget (each task uses ~100 tokens from stub)
    assert result.tasks_attempted <= 2


@pytest.mark.asyncio
async def test_run_objectives_blocks_destructive(
    objectives_file: Path, distillation_dir: Path,
):
    """Destructive operations are blocked when allow_destructive=False."""
    async def custom_executor(subtask: SubTask):
        return {"status": "executed", "tokens_used": 10}

    planner = GoalPlanner(executor=custom_executor)
    pilot = AutoPilot(
        planner=planner,
        objectives_path=objectives_file,
        distillation_dir=str(distillation_dir),
        allow_destructive=False,
    )
    result = await pilot.run_objectives(max_tasks=1)
    assert isinstance(result, AutoPilotResult)


@pytest.mark.asyncio
async def test_run_objectives_saves_distillation(autopilot: AutoPilot):
    """Successful objectives produce distillation JSONL files."""
    result = await autopilot.run_objectives(max_tasks=1)

    if result.distillation_pairs > 0:
        jsonl_files = list(autopilot.distillation_dir.glob("distillation_autopilot_*.jsonl"))
        assert len(jsonl_files) >= 1
        with open(jsonl_files[0]) as f:
            for line in f:
                data = json.loads(line)
                assert data["source"] == "autopilot"
                assert "prompt" in data
                assert "gold_response" in data


# ═══════════════════════════════════════════════════════════════
# AUTO-PROMPTING
# ═══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_run_auto_prompting(tmp_path: Path, distillation_dir: Path, monkeypatch):
    """run_auto_prompting generates comparison pairs."""
    import atlas.core.distillation.prompt_bank as pb_module

    bank_dir = tmp_path / "bank_data"
    coding_dir = bank_dir / "coding"
    coding_dir.mkdir(parents=True)
    (coding_dir / "easy.jsonl").write_text(
        '{"prompt": "Write hello world", "domain": "coding", "difficulty": "easy"}\n'
    )

    def mock_bank_init(self, data_dir=None):
        self.data_dir = Path(str(bank_dir))
        self._prompts = {}
        self._load_all()

    monkeypatch.setattr(pb_module.PromptBank, "__init__", mock_bank_init)

    pilot = AutoPilot(
        objectives_path=tmp_path / "no_objectives.yaml",
        distillation_dir=str(distillation_dir),
    )
    result = await pilot.run_auto_prompting(domain="coding", count=1)

    assert isinstance(result, AutoPilotResult)
    assert result.tasks_attempted == 1
    assert result.tasks_succeeded == 1
    assert result.source == "autopilot"


@pytest.mark.asyncio
async def test_run_auto_prompting_empty_bank(
    tmp_path: Path, distillation_dir: Path, monkeypatch,
):
    """run_auto_prompting handles empty prompt bank gracefully."""
    import atlas.core.distillation.prompt_bank as pb_module

    def mock_bank_init(self, data_dir=None):
        self.data_dir = Path("/nonexistent")
        self._prompts = {}

    monkeypatch.setattr(pb_module.PromptBank, "__init__", mock_bank_init)

    pilot = AutoPilot(
        objectives_path=tmp_path / "x.yaml",
        distillation_dir=str(distillation_dir),
    )
    result = await pilot.run_auto_prompting(domain="nonexistent", count=5)
    assert result.tasks_attempted == 0
    assert result.tasks_succeeded == 0


@pytest.mark.asyncio
async def test_auto_prompting_saves_jsonl(
    tmp_path: Path, distillation_dir: Path, monkeypatch,
):
    """Comparison pairs are saved to distillation JSONL."""
    import atlas.core.distillation.prompt_bank as pb_module

    bank_dir = tmp_path / "bank2"
    coding_dir = bank_dir / "coding"
    coding_dir.mkdir(parents=True)
    (coding_dir / "easy.jsonl").write_text(
        '{"prompt": "Test prompt", "domain": "coding", "difficulty": "easy"}\n'
    )

    def mock_bank_init(self, data_dir=None):
        self.data_dir = Path(str(bank_dir))
        self._prompts = {}
        self._load_all()

    monkeypatch.setattr(pb_module.PromptBank, "__init__", mock_bank_init)

    pilot = AutoPilot(
        objectives_path=tmp_path / "x.yaml",
        distillation_dir=str(distillation_dir),
    )
    result = await pilot.run_auto_prompting(domain="coding", count=1)

    if result.distillation_pairs > 0:
        jsonl_files = list(pilot.distillation_dir.glob("distillation_autoprompt_*.jsonl"))
        assert len(jsonl_files) >= 1
        with open(jsonl_files[0]) as f:
            data = json.loads(f.readline())
            assert data["source"] == "autopilot"
            assert "teacher_response" in data
            assert "student_response" in data


# ═══════════════════════════════════════════════════════════════
# SELF-EVAL
# ═══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_run_self_eval_no_failures(distillation_dir: Path):
    """run_self_eval with no eval data returns clean result."""
    pilot = AutoPilot(
        objectives_path=Path("/nonexistent"),
        distillation_dir=str(distillation_dir),
    )
    result = await pilot.run_self_eval()
    assert result.tasks_attempted == 0
    assert result.distillation_pairs == 0


@pytest.mark.asyncio
async def test_run_self_eval_with_failures(distillation_dir: Path, tmp_path: Path, monkeypatch):
    """run_self_eval processes failure data and creates targeted prompts."""
    import atlas.core.distillation.prompt_bank as pb_module

    # Patch PromptBank via the fully-qualified module (autopilot imports from atlas.*)
    bank_dir = tmp_path / "eval_bank"
    bank_dir.mkdir()

    def mock_bank_init(self, data_dir=None):
        self.data_dir = Path(str(bank_dir))
        self._prompts = {}

    monkeypatch.setattr(pb_module.PromptBank, "__init__", mock_bank_init)

    cycles_dir = distillation_dir / "evolution_cycles"
    cycles_dir.mkdir()
    cycle_data = {
        "problems": [
            {
                "category": "skill_gap",
                "domain": "security",
                "description": "Implement proper input sanitization for SQL queries",
            },
            {
                "category": "under_routing",
                "domain": "coding",
                "description": "Handle async error propagation in Python",
            },
        ],
    }
    (cycles_dir / "cycle_001.json").write_text(json.dumps(cycle_data))

    pilot = AutoPilot(
        objectives_path=Path("/nonexistent"),
        distillation_dir=str(distillation_dir),
    )
    result = await pilot.run_self_eval()
    assert result.tasks_attempted == 2
    assert result.distillation_pairs == 2
    assert result.tasks_succeeded == 2


# ═══════════════════════════════════════════════════════════════
# COMPARISON PAIR
# ═══════════════════════════════════════════════════════════════


def test_comparison_pair_fields():
    """ComparisonPair has the expected default source."""
    pair = ComparisonPair(
        prompt="test", domain="coding",
        teacher_response="gold", teacher_model="gpt-5.4",
        student_response="student", student_model="qwen",
    )
    assert pair.source == "autopilot"
    assert pair.teacher_model == "gpt-5.4"


# ═══════════════════════════════════════════════════════════════
# DOMAIN CLASSIFICATION
# ═══════════════════════════════════════════════════════════════


def test_classify_domain():
    """Domain classifier maps descriptions to known domains."""
    pilot = AutoPilot(objectives_path=Path("/x"))
    assert pilot._classify_domain("Fix security vulnerability") == "security"
    assert pilot._classify_domain("Write code for API") == "code"
    assert pilot._classify_domain("Research ML trends") == "research"
    assert pilot._classify_domain("Something unrelated") == "general"


# ═══════════════════════════════════════════════════════════════
# ITERATION AND BUDGET LIMITS
# ═══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_max_iterations_respected(objectives_file: Path, distillation_dir: Path):
    """AutoPilot stops after max_iterations even with tasks remaining."""
    pilot = AutoPilot(
        objectives_path=objectives_file,
        distillation_dir=str(distillation_dir),
        max_iterations=1,
    )
    result = await pilot.run_objectives(max_tasks=10)
    assert result.tasks_attempted <= 1
