#!/usr/bin/env python3
"""
Tests for research action pipeline and code proposer.

Covers:
- Action classification (code_change / config_change / skill_improvement)
- Risk assessment (low / medium / high)
- Auto-applicability gating
- Pipeline end-to-end processing
- Code proposer safety gates
- Novel source methods (HN, Reddit, GitHub)
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure atlas package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.evolution.research_pipeline import (
    ActionType,
    ClassifiedAction,
    PipelineResult,
    ResearchActionPipeline,
    RiskLevel,
    _classify_action,
    _extract_target_file,
)
from core.evolution.code_proposer import (
    CodeProposer,
    Proposal,
    ProposerCycleResult,
    _ALLOWED_AUTO_FILES,
)


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def config_action():
    """Action item that should classify as config_change."""
    return {
        "finding_index": 1,
        "action": "Bump coding domain weight in scorer_weights.yaml from 0.10 to 0.15",
        "category": "upgrade",
        "effort": "quick_win",
        "impact": "high",
        "ties_to": "routing accuracy",
    }


@pytest.fixture
def code_action():
    """Action item that should classify as code_change."""
    return {
        "finding_index": 2,
        "action": "Implement new batch endpoint in the provider class to reduce API calls",
        "category": "new_capability",
        "effort": "medium",
        "impact": "medium",
        "ties_to": "cost reduction",
    }


@pytest.fixture
def skill_action():
    """Action item that should classify as skill_improvement."""
    return {
        "finding_index": 3,
        "action": "Update copywriting SKILL.md to include new tone criteria for B2B",
        "category": "client_value",
        "effort": "quick_win",
        "impact": "medium",
        "ties_to": "skill quality",
    }


@pytest.fixture
def security_action():
    """Action item with high-risk security content."""
    return {
        "finding_index": 4,
        "action": "Patch trust_gate security vulnerability in authentication flow",
        "category": "security",
        "effort": "major",
        "impact": "high",
        "ties_to": "production security",
    }


@pytest.fixture
def mixed_actions(config_action, code_action, skill_action, security_action):
    """Mix of all action types."""
    return [config_action, code_action, skill_action, security_action]


@pytest.fixture
def tmp_log_dir(tmp_path):
    """Temporary log directory."""
    log_dir = tmp_path / "research_actions"
    log_dir.mkdir()
    return str(log_dir)


@pytest.fixture
def tmp_proposal_dir(tmp_path):
    """Temporary proposal log directory."""
    proposal_dir = tmp_path / "code_proposals"
    proposal_dir.mkdir()
    return str(proposal_dir)


@pytest.fixture
def sample_report(tmp_path):
    """A sample research report JSON file."""
    report = {
        "timestamp": "2026-04-01T00:00:00Z",
        "total_findings": 5,
        "action_items": [
            {
                "finding_index": 1,
                "action": "Bump coding weight in scorer_weights.yaml to 0.15",
                "category": "upgrade",
                "effort": "quick_win",
                "impact": "high",
                "ties_to": "routing",
            },
            {
                "finding_index": 2,
                "action": "Update SKILL.md prompt template for better formatting",
                "category": "client_value",
                "effort": "quick_win",
                "impact": "medium",
                "ties_to": "quality",
            },
        ],
        "findings": [],
    }
    path = tmp_path / "research_2026-04-01.json"
    with open(path, "w") as f:
        json.dump(report, f)
    return str(path)


# ═══════════════════════════════════════════════════════════════
# CLASSIFICATION TESTS
# ═══════════════════════════════════════════════════════════════


def test_classify_config_action(config_action):
    """Config-related actions are classified as CONFIG_CHANGE."""
    result = _classify_action(config_action)
    assert result.action_type == ActionType.CONFIG_CHANGE


def test_classify_code_action(code_action):
    """Code-related actions are classified as CODE_CHANGE."""
    result = _classify_action(code_action)
    assert result.action_type == ActionType.CODE_CHANGE


def test_classify_skill_action(skill_action):
    """Skill-related actions are classified as SKILL_IMPROVEMENT."""
    result = _classify_action(skill_action)
    assert result.action_type == ActionType.SKILL_IMPROVEMENT


def test_classify_empty_action():
    """Empty action defaults to SKILL_IMPROVEMENT."""
    result = _classify_action({"action": "", "category": "", "effort": ""})
    assert result.action_type == ActionType.SKILL_IMPROVEMENT


def test_classify_preserves_original(config_action):
    """Original dict is preserved in the classified action."""
    result = _classify_action(config_action)
    assert result.original == config_action


# ═══════════════════════════════════════════════════════════════
# RISK ASSESSMENT TESTS
# ═══════════════════════════════════════════════════════════════


def test_risk_high_for_security(security_action):
    """Security-related actions are HIGH risk."""
    result = _classify_action(security_action)
    assert result.risk == RiskLevel.HIGH


def test_risk_low_for_config_known_file(config_action):
    """Config changes to known safe files are LOW risk."""
    result = _classify_action(config_action)
    assert result.risk == RiskLevel.LOW


def test_risk_medium_for_code(code_action):
    """Code changes are at least MEDIUM risk."""
    result = _classify_action(code_action)
    assert result.risk in (RiskLevel.MEDIUM, RiskLevel.HIGH)


def test_risk_high_for_production():
    """Actions mentioning production are HIGH risk."""
    action = {
        "action": "Deploy new config to production database",
        "category": "infrastructure",
        "effort": "major",
    }
    result = _classify_action(action)
    assert result.risk == RiskLevel.HIGH


# ═══════════════════════════════════════════════════════════════
# AUTO-APPLICABILITY TESTS
# ═══════════════════════════════════════════════════════════════


def test_auto_applicable_config_low_risk(config_action):
    """Low-risk config changes to known files are auto-applicable."""
    result = _classify_action(config_action)
    assert result.auto_applicable is True


def test_not_auto_applicable_code(code_action):
    """Code changes are never auto-applicable."""
    result = _classify_action(code_action)
    assert result.auto_applicable is False


def test_not_auto_applicable_high_risk(security_action):
    """High-risk actions are never auto-applicable."""
    result = _classify_action(security_action)
    assert result.auto_applicable is False


def test_not_auto_applicable_unknown_file():
    """Config changes to unknown files are not auto-applicable."""
    action = {
        "action": "Update custom_config.yaml threshold values",
        "category": "upgrade",
        "effort": "quick_win",
    }
    result = _classify_action(action)
    assert result.auto_applicable is False


# ═══════════════════════════════════════════════════════════════
# TARGET FILE EXTRACTION
# ═══════════════════════════════════════════════════════════════


def test_extract_scorer_weights():
    """Extracts scorer_weights.yaml from action text."""
    target = _extract_target_file("bump weight in scorer_weights to 0.25")
    assert target == "config/scorer_weights.yaml"


def test_extract_routing_config():
    """Extracts routing_config.yaml from action text."""
    target = _extract_target_file("update routing_config provider list")
    assert target == "config/routing_config.yaml"


def test_extract_path_pattern():
    """Extracts file paths from text."""
    target = _extract_target_file("modify atlas/core/routing/enricher.py function")
    assert target == "atlas/core/routing/enricher.py"


def test_extract_empty_for_no_path():
    """Returns empty string when no path is found."""
    target = _extract_target_file("do something general")
    assert target == ""


# ═══════════════════════════════════════════════════════════════
# PIPELINE END-TO-END
# ═══════════════════════════════════════════════════════════════


def test_pipeline_process_list(mixed_actions, tmp_log_dir):
    """Pipeline processes a list of actions."""
    pipeline = ResearchActionPipeline(log_dir=tmp_log_dir)
    result = asyncio.run(pipeline.process(mixed_actions))

    assert result.actions_received == 4
    assert result.actions_classified == 4
    assert len(result.code_changes) >= 1
    assert len(result.config_changes) >= 1
    assert len(result.skill_improvements) >= 1


def test_pipeline_process_dict(config_action, tmp_log_dir):
    """Pipeline processes a dict with action_items key."""
    pipeline = ResearchActionPipeline(log_dir=tmp_log_dir)
    result = asyncio.run(pipeline.process({"action_items": [config_action]}))

    assert result.actions_received == 1
    assert result.actions_classified == 1


def test_pipeline_process_file(sample_report, tmp_log_dir):
    """Pipeline processes a report JSON file path."""
    pipeline = ResearchActionPipeline(log_dir=tmp_log_dir)
    result = asyncio.run(pipeline.process(sample_report))

    assert result.actions_received == 2
    assert result.actions_classified == 2


def test_pipeline_empty_input(tmp_log_dir):
    """Pipeline handles empty input gracefully."""
    pipeline = ResearchActionPipeline(log_dir=tmp_log_dir)
    result = asyncio.run(pipeline.process([]))

    assert result.actions_received == 0
    assert result.actions_classified == 0


def test_pipeline_logs_result(mixed_actions, tmp_log_dir):
    """Pipeline logs result to disk."""
    pipeline = ResearchActionPipeline(log_dir=tmp_log_dir)
    asyncio.run(pipeline.process(mixed_actions))

    log_files = list(Path(tmp_log_dir).glob("pipeline_*.json"))
    assert len(log_files) == 1

    with open(log_files[0]) as f:
        data = json.load(f)
    assert data["actions_received"] == 4


# ═══════════════════════════════════════════════════════════════
# CODE PROPOSER TESTS
# ═══════════════════════════════════════════════════════════════


def test_proposer_skips_high_risk(tmp_proposal_dir):
    """Proposer skips HIGH risk actions."""
    proposer = CodeProposer(log_dir=tmp_proposal_dir, dry_run=True)
    result = asyncio.run(proposer.propose({
        "description": "Patch trust_gate security vulnerability",
        "target_file": "atlas/core/security/trust_gate.py",
        "risk": "high",
        "action_type": "code_change",
    }))

    assert result["status"] == "skipped"


def test_proposer_flags_non_allowlist_file(tmp_proposal_dir):
    """Proposer flags files not in the auto-apply allowlist."""
    proposer = CodeProposer(log_dir=tmp_proposal_dir, dry_run=True)
    result = asyncio.run(proposer.propose({
        "description": "Update enricher rules",
        "target_file": "atlas/core/routing/prompt_enricher.py",
        "risk": "medium",
        "action_type": "config_change",
    }))

    assert result["status"] == "proposed"
    assert "not in auto-apply allowlist" in result["reason"]


def test_proposer_dry_run(tmp_proposal_dir):
    """Proposer in dry_run mode generates description but doesn't create PR."""
    proposer = CodeProposer(log_dir=tmp_proposal_dir, dry_run=True)
    result = asyncio.run(proposer.propose({
        "description": "Bump scorer weight",
        "target_file": "config/scorer_weights.yaml",
        "risk": "low",
        "action_type": "config_change",
    }))

    assert result["status"] == "dry_run"
    assert "patch_description" in result


def test_proposer_logs_proposal(tmp_proposal_dir):
    """Proposer logs every proposal to disk."""
    proposer = CodeProposer(log_dir=tmp_proposal_dir, dry_run=True)
    asyncio.run(proposer.propose({
        "description": "Test proposal",
        "target_file": "config/scorer_weights.yaml",
        "risk": "low",
        "action_type": "config_change",
    }))

    log_files = list(Path(tmp_proposal_dir).glob("prop_*.json"))
    assert len(log_files) == 1

    with open(log_files[0]) as f:
        data = json.load(f)
    assert data["status"] == "dry_run"


def test_proposer_rate_limits(tmp_proposal_dir):
    """Proposer stops opening PRs after hitting the per-cycle limit."""
    proposer = CodeProposer(log_dir=tmp_proposal_dir, dry_run=True)
    # Simulate hitting the limit
    proposer._prs_this_cycle = 3

    result = asyncio.run(proposer.propose({
        "description": "Bump weight",
        "target_file": "config/scorer_weights.yaml",
        "risk": "low",
        "action_type": "config_change",
    }))

    assert result["status"] == "deferred"
    assert "PR limit" in result["reason"]


def test_proposer_batch(tmp_proposal_dir):
    """Batch processing handles multiple improvements."""
    proposer = CodeProposer(log_dir=tmp_proposal_dir, dry_run=True)
    improvements = [
        {"description": "Bump weight A", "target_file": "config/scorer_weights.yaml", "risk": "low", "action_type": "config_change"},
        {"description": "Patch security", "target_file": "atlas/core/security/trust_gate.py", "risk": "high", "action_type": "code_change"},
        {"description": "Update skill", "target_file": "atlas/skills/library/copywriting/SKILL.md", "risk": "medium", "action_type": "skill_improvement"},
    ]

    result = asyncio.run(proposer.propose_batch(improvements))
    assert result.proposals_received == 3
    assert result.skipped_high_risk == 1


# ═══════════════════════════════════════════════════════════════
# ALLOWED FILES SAFETY
# ═══════════════════════════════════════════════════════════════


def test_allowed_auto_files_are_yaml_only():
    """Auto-apply allowlist contains only YAML config files."""
    for f in _ALLOWED_AUTO_FILES:
        assert f.endswith(".yaml"), f"Non-YAML file in allowlist: {f}"
        assert f.startswith("config/"), f"Non-config file in allowlist: {f}"


def test_no_python_in_allowed_files():
    """No .py files in the auto-apply allowlist."""
    for f in _ALLOWED_AUTO_FILES:
        assert not f.endswith(".py"), f"Python file in allowlist: {f}"


# ═══════════════════════════════════════════════════════════════
# NOVEL SOURCES (HN, Reddit, GitHub)
# ═══════════════════════════════════════════════════════════════


def test_hackernews_search_parses_response(tmp_path):
    """HN search correctly parses Algolia API response."""
    from core.evolution.weekly_research import WeeklyResearchScout

    scout = WeeklyResearchScout(report_dir=str(tmp_path / "reports"))

    mock_response = json.dumps({
        "hits": [
            {
                "objectID": "12345",
                "title": "Qwen 3.5 Released with Major Improvements",
                "url": "https://example.com/qwen",
                "points": 100,
                "created_at_i": 1711900000,
            },
            {
                "objectID": "12346",
                "title": "Random unrelated post",
                "url": "https://example.com/random",
                "points": 5,
                "created_at_i": 1711900000,
            },
        ]
    }).encode()

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_response
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        findings = asyncio.run(scout._search_hackernews("Qwen"))

    # Should find the Qwen post (high relevance), may filter the random one
    assert len(findings) >= 1
    assert findings[0].source == "hackernews"
    assert "Qwen" in findings[0].title


def test_reddit_search_parses_response(tmp_path):
    """Reddit search correctly parses JSON API response."""
    from core.evolution.weekly_research import WeeklyResearchScout

    scout = WeeklyResearchScout(report_dir=str(tmp_path / "reports"))

    mock_response = json.dumps({
        "data": {
            "children": [
                {
                    "data": {
                        "title": "New Unsloth Release Supports Qwen 3.5",
                        "selftext": "Major update with dynamic quants",
                        "score": 150,
                        "permalink": "/r/LocalLLaMA/comments/abc/test/",
                    }
                },
            ]
        }
    }).encode()

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_response
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        findings = asyncio.run(scout._search_reddit("LocalLLaMA", "Unsloth"))

    assert len(findings) >= 1
    assert findings[0].source == "reddit"
    assert "Unsloth" in findings[0].title


def test_github_trending_parses_response(tmp_path):
    """GitHub trending search correctly parses API response."""
    from core.evolution.weekly_research import WeeklyResearchScout

    scout = WeeklyResearchScout(report_dir=str(tmp_path / "reports"))

    mock_response = json.dumps({
        "items": [
            {
                "full_name": "user/qwen-finetuner",
                "description": "Fine-tune Qwen models with LoRA and Unsloth",
                "stargazers_count": 500,
                "html_url": "https://github.com/user/qwen-finetuner",
                "language": "Python",
            },
        ]
    }).encode()

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_response
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        findings = asyncio.run(scout._search_github_trending())

    assert len(findings) >= 1
    assert findings[0].source == "github"
    assert "qwen-finetuner" in findings[0].title


def test_hackernews_handles_network_error(tmp_path):
    """HN search handles network errors gracefully."""
    from core.evolution.weekly_research import WeeklyResearchScout

    scout = WeeklyResearchScout(report_dir=str(tmp_path / "reports"))

    with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
        findings = asyncio.run(scout._search_hackernews("test"))

    assert findings == []


def test_detect_source_hackernews(tmp_path):
    """_detect_source recognizes HackerNews URLs."""
    from core.evolution.weekly_research import WeeklyResearchScout

    scout = WeeklyResearchScout(report_dir=str(tmp_path / "reports"))
    assert scout._detect_source("https://news.ycombinator.com/item?id=123") == "hackernews"


# ═══════════════════════════════════════════════════════════════
# DAEMON INTEGRATION
# ═══════════════════════════════════════════════════════════════


def test_daemon_research_pipeline_step(tmp_path):
    """Daemon's _run_research_pipeline handles missing reports gracefully."""
    from core.evolution.daemon import EvolutionDaemon, EvolutionConfig

    config = EvolutionConfig(
        cycle_log_dir=str(tmp_path / "cycles"),
        weights_path=str(tmp_path / "weights.yaml"),
    )
    daemon = EvolutionDaemon(config=config)

    # Should not raise even with no reports
    asyncio.run(daemon._run_research_pipeline())
