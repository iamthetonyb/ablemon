#!/usr/bin/env python3
"""Tests for the prompt bank used in active corpus generation."""

import json
import os
import sys
from pathlib import Path

import pytest

# Ensure able package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from able.core.distillation.prompt_bank import PromptBank, PromptEntry


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def real_bank() -> PromptBank:
    """Load the actual prompt bank from the repo data files."""
    data_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "core",
        "distillation",
        "prompt_bank_data",
    )
    return PromptBank(data_dir=data_dir)


@pytest.fixture
def tmp_bank(tmp_path: Path) -> PromptBank:
    """Create a temporary prompt bank with known data."""
    coding_dir = tmp_path / "coding"
    coding_dir.mkdir()

    easy_path = coding_dir / "easy.jsonl"
    easy_path.write_text(
        '{"prompt": "Write hello world.", "domain": "coding", "difficulty": "easy", "tags": ["python"]}\n'
        '{"prompt": "FizzBuzz.", "domain": "coding", "difficulty": "easy", "tags": ["python"]}\n'
    )

    hard_path = coding_dir / "hard.jsonl"
    hard_path.write_text(
        '{"prompt": "Build a compiler.", "domain": "coding", "difficulty": "hard", "tags": ["compilers"]}\n'
    )

    security_dir = tmp_path / "security"
    security_dir.mkdir()
    med_path = security_dir / "medium.jsonl"
    med_path.write_text(
        '{"prompt": "Implement CSRF protection.", "domain": "security", "difficulty": "medium", "tags": ["csrf"]}\n'
    )

    return PromptBank(data_dir=str(tmp_path))


# ═══════════════════════════════════════════════════════════════
# LOADING TESTS
# ═══════════════════════════════════════════════════════════════


def test_loads_all_jsonl_files(real_bank: PromptBank):
    """PromptBank loads all JSONL files from data directory."""
    assert real_bank.count() > 0, "Real prompt bank should have prompts"


def test_all_domains_returns_available(real_bank: PromptBank):
    """all_domains() returns all domains that have data files."""
    domains = real_bank.all_domains()
    assert len(domains) >= 4, f"Expected at least 4 domains, got {domains}"
    for d in ["coding", "security", "reasoning", "creative"]:
        assert d in domains, f"Missing domain: {d}"


def test_loads_from_tmp_dir(tmp_bank: PromptBank):
    """PromptBank loads from a custom data directory."""
    assert tmp_bank.count() == 4


def test_ignores_empty_duplicate_domain_dirs(tmp_path: Path):
    (tmp_path / "coding").mkdir()
    (tmp_path / "coding" / "easy.jsonl").write_text(
        '{"prompt": "Write hello world.", "domain": "coding", "difficulty": "easy"}\n'
    )
    (tmp_path / "coding 2").mkdir()

    bank = PromptBank(data_dir=str(tmp_path))

    assert bank.all_domains() == ["coding"]


def test_dedupes_duplicate_prompts_on_load(tmp_path: Path):
    coding_dir = tmp_path / "coding"
    coding_dir.mkdir()
    (coding_dir / "medium.jsonl").write_text(
        '{"prompt": "Handle async errors.", "domain": "coding", "difficulty": "medium"}\n'
        '{"prompt": "Handle async errors.", "domain": "coding", "difficulty": "medium"}\n'
    )

    bank = PromptBank(data_dir=str(tmp_path))

    assert bank.count(domain="coding", difficulty="medium") == 1


# ═══════════════════════════════════════════════════════════════
# SAMPLE TESTS
# ═══════════════════════════════════════════════════════════════


def test_sample_returns_correct_count(tmp_bank: PromptBank):
    """sample() returns the requested number of prompts."""
    result = tmp_bank.sample(n=2)
    assert len(result) == 2


def test_sample_caps_at_pool_size(tmp_bank: PromptBank):
    """sample() returns fewer than n if pool is smaller."""
    result = tmp_bank.sample(n=100)
    assert len(result) == 4


def test_sample_domain_filter(tmp_bank: PromptBank):
    """sample() with domain filter only returns matching prompts."""
    result = tmp_bank.sample(domain="security", n=10)
    assert len(result) == 1
    assert result[0].domain == "security"


def test_sample_difficulty_filter(tmp_bank: PromptBank):
    """sample() with difficulty filter only returns matching prompts."""
    result = tmp_bank.sample(difficulty="easy", n=10)
    assert len(result) == 2
    assert all(p.difficulty == "easy" for p in result)


def test_sample_domain_and_difficulty_filter(tmp_bank: PromptBank):
    """sample() with both filters narrows correctly."""
    result = tmp_bank.sample(domain="coding", difficulty="hard", n=10)
    assert len(result) == 1
    assert result[0].domain == "coding"
    assert result[0].difficulty == "hard"


def test_sample_nonexistent_domain(tmp_bank: PromptBank):
    """sample() returns empty for a domain with no data."""
    result = tmp_bank.sample(domain="tools", n=10)
    assert result == []


# ═══════════════════════════════════════════════════════════════
# COUNT TESTS
# ═══════════════════════════════════════════════════════════════


def test_count_total(tmp_bank: PromptBank):
    """count() returns total prompt count."""
    assert tmp_bank.count() == 4


def test_count_by_domain(tmp_bank: PromptBank):
    """count() with domain filter returns domain-specific count."""
    assert tmp_bank.count(domain="coding") == 3
    assert tmp_bank.count(domain="security") == 1


def test_count_by_difficulty(tmp_bank: PromptBank):
    """count() with difficulty filter works across domains."""
    assert tmp_bank.count(difficulty="easy") == 2
    assert tmp_bank.count(difficulty="hard") == 1
    assert tmp_bank.count(difficulty="medium") == 1


# ═══════════════════════════════════════════════════════════════
# ADD PROMPT TESTS
# ═══════════════════════════════════════════════════════════════


def test_add_prompt_persists(tmp_path: Path):
    """add_prompt() writes to JSONL file on disk."""
    bank = PromptBank(data_dir=str(tmp_path))
    entry = PromptEntry(
        prompt="Test prompt",
        domain="reasoning",
        difficulty="easy",
        tags=["test"],
    )
    bank.add_prompt(entry)

    assert bank.count() == 1
    assert bank.count(domain="reasoning") == 1

    # Verify file exists and contains valid JSON
    jsonl_path = tmp_path / "reasoning" / "easy.jsonl"
    assert jsonl_path.exists()
    data = json.loads(jsonl_path.read_text().strip())
    assert data["prompt"] == "Test prompt"


def test_add_prompt_appends(tmp_bank: PromptBank):
    """add_prompt() appends to existing data without overwriting."""
    initial = tmp_bank.count(domain="coding", difficulty="easy")
    entry = PromptEntry(
        prompt="New coding prompt",
        domain="coding",
        difficulty="easy",
    )
    tmp_bank.add_prompt(entry)
    assert tmp_bank.count(domain="coding", difficulty="easy") == initial + 1


def test_add_prompt_skips_duplicate(tmp_bank: PromptBank):
    initial = tmp_bank.count(domain="coding", difficulty="easy")
    entry = PromptEntry(
        prompt="Write hello world.",
        domain="coding",
        difficulty="easy",
        tags=["python"],
    )
    tmp_bank.add_prompt(entry)
    assert tmp_bank.count(domain="coding", difficulty="easy") == initial


# ═══════════════════════════════════════════════════════════════
# ADD FROM FAILURES TESTS
# ═══════════════════════════════════════════════════════════════


def test_add_from_failures(tmp_path: Path):
    """add_from_failures() creates prompts from failure patterns."""
    bank = PromptBank(data_dir=str(tmp_path))
    patterns = [
        {
            "category": "skill_gap",
            "domain": "coding",
            "description": "Write async Python code with proper error handling.",
            "difficulty": "medium",
            "tags": ["async", "error-handling"],
        },
        {
            "category": "under_routing",
            "domain": "security",
            "description": "Implement input validation for a REST API.",
        },
    ]
    added = bank.add_from_failures(patterns)
    assert added == 2
    assert bank.count() == 2


def test_add_from_failures_skips_empty(tmp_path: Path):
    """add_from_failures() skips patterns without a description."""
    bank = PromptBank(data_dir=str(tmp_path))
    patterns = [
        {"category": "skill_gap", "domain": "coding", "description": ""},
        {"category": "skill_gap", "domain": "coding"},
    ]
    added = bank.add_from_failures(patterns)
    assert added == 0


# ═══════════════════════════════════════════════════════════════
# EMPTY BANK TESTS
# ═══════════════════════════════════════════════════════════════


def test_empty_bank_handles_gracefully(tmp_path: Path):
    """Empty prompt bank returns sensible defaults."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    bank = PromptBank(data_dir=str(empty_dir))

    assert bank.count() == 0
    assert bank.all_domains() == []
    assert bank.sample(n=5) == []


def test_nonexistent_dir_handles_gracefully(tmp_path: Path):
    """Non-existent data dir doesn't crash."""
    bank = PromptBank(data_dir=str(tmp_path / "does_not_exist"))
    assert bank.count() == 0
    assert bank.all_domains() == []


# ═══════════════════════════════════════════════════════════════
# JSONL VALIDATION TESTS
# ═══════════════════════════════════════════════════════════════


def test_all_jsonl_files_are_valid(real_bank: PromptBank):
    """Every line in every JSONL file is valid JSON with required fields."""
    data_dir = Path(real_bank.data_dir)
    for jsonl_file in data_dir.rglob("*.jsonl"):
        with open(jsonl_file, "r", encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                assert "prompt" in data, f"{jsonl_file}:{i} missing 'prompt'"
                assert "domain" in data, f"{jsonl_file}:{i} missing 'domain'"
                assert "difficulty" in data, f"{jsonl_file}:{i} missing 'difficulty'"
                assert isinstance(data["prompt"], str) and len(data["prompt"]) > 0


def test_prompt_entry_round_trip():
    """PromptEntry serializes and deserializes correctly."""
    original = PromptEntry(
        prompt="Test",
        domain="coding",
        difficulty="hard",
        expected_skills=["code-review"],
        tags=["python"],
    )
    d = original.to_dict()
    restored = PromptEntry.from_dict(d)
    assert restored.prompt == original.prompt
    assert restored.domain == original.domain
    assert restored.difficulty == original.difficulty
    assert restored.expected_skills == original.expected_skills
    assert restored.tags == original.tags
