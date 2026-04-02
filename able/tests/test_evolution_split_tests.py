#!/usr/bin/env python3
"""
Tests for evolution daemon split test integration.

Covers:
- Policy decisions (should_split_test)
- Proposal creation and persistence
- Consistent hashing for group assignment
- Outcome recording and auto-conclusion
- Daemon integration (split_policy gating)
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure able package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.evolution.split_test_integration import (
    EvolutionSplitTestPolicy,
    SplitTestProposal,
    WeightChange,
)
from core.evolution.improver import Improvement


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def tmp_proposals_dir(tmp_path):
    """Create a temporary proposals directory."""
    proposals_dir = tmp_path / "proposals"
    proposals_dir.mkdir()
    return str(proposals_dir)


@pytest.fixture
def policy(tmp_proposals_dir):
    """Policy with low min_samples for fast testing."""
    return EvolutionSplitTestPolicy(
        min_samples=5,
        split_test_threshold=0.10,
        proposals_dir=tmp_proposals_dir,
    )


@pytest.fixture
def small_improvement():
    """Improvement with < 10% change."""
    return Improvement(
        target="features.requires_code_weight",
        current_value=0.20,
        proposed_value=0.21,
        change_pct=0.05,
        reason="minor tuning",
        source="rule_based",
    )


@pytest.fixture
def large_improvement():
    """Improvement with > 10% change."""
    return Improvement(
        target="features.requires_code_weight",
        current_value=0.20,
        proposed_value=0.25,
        change_pct=0.25,
        reason="significant tuning based on eval data",
        source="rule_based",
    )


@pytest.fixture
def security_improvement():
    """Security weight increase."""
    return Improvement(
        target="features.safety_critical_weight",
        current_value=0.30,
        proposed_value=0.36,
        change_pct=0.20,
        reason="security under-routing detected",
        source="rule_based",
    )


@pytest.fixture
def threshold_improvement():
    """Tier threshold change."""
    return Improvement(
        target="tier_thresholds.tier_1_max",
        current_value=0.40,
        proposed_value=0.38,
        change_pct=-0.05,
        reason="lower tier 1 ceiling",
        source="rule_based",
    )


@pytest.fixture
def current_weights():
    """Baseline weights config."""
    return {
        "version": 2,
        "features": {
            "token_count_weight": 0.15,
            "requires_tools_weight": 0.15,
            "requires_code_weight": 0.20,
            "multi_step_weight": 0.20,
            "safety_critical_weight": 0.30,
        },
        "tier_thresholds": {
            "tier_1_max": 0.40,
            "tier_2_max": 0.70,
        },
        "domain_adjustments": {
            "security": 0.20,
            "coding": 0.10,
        },
    }


# ═══════════════════════════════════════════════════════════════
# SHOULD_SPLIT_TEST TESTS
# ═══════════════════════════════════════════════════════════════


def test_should_split_test_large_change(policy, large_improvement):
    """Changes > 10% require split testing."""
    assert policy.should_split_test([large_improvement]) is True


def test_should_split_test_small_change(policy, small_improvement):
    """Changes < 10% do NOT require split testing."""
    assert policy.should_split_test([small_improvement]) is False


def test_should_split_test_threshold_change(policy, threshold_improvement):
    """Tier threshold changes always require split testing."""
    assert policy.should_split_test([threshold_improvement]) is True


def test_security_increase_bypasses_split_test(policy, security_improvement):
    """Security weight increases bypass split testing — deploy immediately."""
    assert policy.should_split_test([security_improvement]) is False


def test_security_decrease_does_not_bypass(policy):
    """Security weight *decreases* should still be split tested if large."""
    imp = Improvement(
        target="features.safety_critical_weight",
        current_value=0.30,
        proposed_value=0.24,
        change_pct=-0.20,
        reason="over-routing detected",
        source="rule_based",
    )
    assert policy.should_split_test([imp]) is True


def test_mixed_improvements_triggers_if_any_large(
    policy, small_improvement, large_improvement
):
    """If any single improvement is large, the whole batch triggers."""
    assert policy.should_split_test([small_improvement, large_improvement]) is True


def test_empty_improvements(policy):
    """Empty list never triggers split test."""
    assert policy.should_split_test([]) is False


def test_dict_improvements(policy):
    """Policy works with dict-shaped improvements too."""
    imp_dict = {
        "target": "features.requires_code_weight",
        "change_pct": 0.25,
        "current_value": 0.20,
        "proposed_value": 0.25,
    }
    assert policy.should_split_test([imp_dict]) is True


# ═══════════════════════════════════════════════════════════════
# PROPOSAL CREATION
# ═══════════════════════════════════════════════════════════════


def test_create_proposal(policy, large_improvement, current_weights):
    """Proposal stores correct control and experiment configs."""
    proposal = policy.create_proposal(
        [large_improvement], current_weights, "test reason"
    )

    assert proposal.status == "running"
    assert proposal.reason == "test reason"
    assert len(proposal.changes) == 1
    assert proposal.changes[0].feature == "features.requires_code_weight"
    assert proposal.changes[0].old_value == 0.20
    assert proposal.changes[0].new_value == 0.25

    # Control should have original weights
    assert proposal.control_config["features"]["requires_code_weight"] == 0.20

    # Experiment should have the proposed value
    assert proposal.experiment_config["features"]["requires_code_weight"] == 0.25

    # Other weights unchanged in experiment
    assert proposal.experiment_config["features"]["token_count_weight"] == 0.15


def test_proposal_persisted_to_disk(policy, large_improvement, current_weights):
    """Proposal is saved as JSON in the proposals directory."""
    proposal = policy.create_proposal(
        [large_improvement], current_weights, "persistence test"
    )

    path = Path(policy.proposals_dir) / f"{proposal.id}.json"
    assert path.exists()

    with open(path) as f:
        data = json.load(f)
    assert data["id"] == proposal.id
    assert data["status"] == "running"
    assert len(data["changes"]) == 1


def test_proposal_id_contains_hash(policy, large_improvement, current_weights):
    """Proposal ID is deterministic from changes."""
    proposal = policy.create_proposal(
        [large_improvement], current_weights, "id test"
    )
    assert proposal.id.startswith("split_")
    assert len(proposal.id) > 20  # timestamp + hash


# ═══════════════════════════════════════════════════════════════
# GROUP ASSIGNMENT
# ═══════════════════════════════════════════════════════════════


def test_consistent_hashing_deterministic(policy):
    """Same proposal_id + request_hash always returns same group."""
    group1 = policy.assign_group("proposal_1", "req_abc")
    group2 = policy.assign_group("proposal_1", "req_abc")
    assert group1 == group2


def test_consistent_hashing_valid_groups(policy):
    """assign_group only returns 'control' or 'experiment'."""
    groups = set()
    for i in range(100):
        group = policy.assign_group("proposal_1", f"req_{i}")
        groups.add(group)

    assert groups.issubset({"control", "experiment"})


def test_consistent_hashing_distributes(policy):
    """Hash should produce both groups across many requests."""
    groups = set()
    for i in range(100):
        group = policy.assign_group("proposal_1", f"req_{i}")
        groups.add(group)

    # With 100 requests, we should see both groups
    assert "control" in groups
    assert "experiment" in groups


# ═══════════════════════════════════════════════════════════════
# OUTCOME RECORDING
# ═══════════════════════════════════════════════════════════════


def test_record_outcome(policy, large_improvement, current_weights):
    """Outcomes are recorded to the correct group."""
    proposal = policy.create_proposal(
        [large_improvement], current_weights, "recording test"
    )

    policy.record_outcome(proposal.id, "control", success=True, latency_ms=100)
    policy.record_outcome(proposal.id, "control", success=False, latency_ms=200)
    policy.record_outcome(proposal.id, "experiment", success=True, latency_ms=50)

    # Reload from disk to verify persistence
    loaded = policy._load_proposals(status="running")
    assert len(loaded) == 1

    results = loaded[0].results
    assert len(results["control"]) == 2
    assert len(results["experiment"]) == 1
    assert results["control"][0]["success"] is True
    assert results["control"][1]["latency_ms"] == 200


def test_record_outcome_nonexistent_proposal(policy):
    """Recording to a non-existent proposal logs warning, doesn't crash."""
    # Should not raise
    policy.record_outcome("nonexistent_id", "control", success=True)


# ═══════════════════════════════════════════════════════════════
# AUTO-CONCLUDE & WINNER DETERMINATION
# ═══════════════════════════════════════════════════════════════


def test_auto_conclude_after_min_samples(
    policy, large_improvement, current_weights
):
    """Test auto-concludes when both groups reach min_samples."""
    proposal = policy.create_proposal(
        [large_improvement], current_weights, "conclude test"
    )

    # Record enough outcomes for both groups
    for _ in range(5):
        policy.record_outcome(proposal.id, "control", success=True)
    for _ in range(5):
        policy.record_outcome(proposal.id, "experiment", success=True)

    concluded = policy.check_running_tests()
    assert len(concluded) == 1
    assert concluded[0]["id"] == proposal.id


def test_no_conclude_below_min_samples(
    policy, large_improvement, current_weights
):
    """Does not conclude until both groups have enough samples."""
    proposal = policy.create_proposal(
        [large_improvement], current_weights, "not yet"
    )

    # Only control has enough
    for _ in range(5):
        policy.record_outcome(proposal.id, "control", success=True)
    for _ in range(3):
        policy.record_outcome(proposal.id, "experiment", success=True)

    concluded = policy.check_running_tests()
    assert len(concluded) == 0


def test_winner_experiment_higher_success_rate(
    policy, large_improvement, current_weights
):
    """Experiment wins when it has higher success rate."""
    proposal = policy.create_proposal(
        [large_improvement], current_weights, "experiment wins"
    )

    # Control: 3/5 success = 60%
    for _ in range(3):
        policy.record_outcome(proposal.id, "control", success=True)
    for _ in range(2):
        policy.record_outcome(proposal.id, "control", success=False)

    # Experiment: 5/5 success = 100%
    for _ in range(5):
        policy.record_outcome(proposal.id, "experiment", success=True)

    result = policy.conclude_test(proposal.id)
    assert result["winner"] == "experiment"
    assert result["experiment_success_rate"] == 1.0
    assert result["control_success_rate"] == 0.6


def test_winner_control_higher_success_rate(
    policy, large_improvement, current_weights
):
    """Control wins when it has higher success rate."""
    proposal = policy.create_proposal(
        [large_improvement], current_weights, "control wins"
    )

    # Control: 5/5 = 100%
    for _ in range(5):
        policy.record_outcome(proposal.id, "control", success=True)

    # Experiment: 2/5 = 40%
    for _ in range(2):
        policy.record_outcome(proposal.id, "experiment", success=True)
    for _ in range(3):
        policy.record_outcome(proposal.id, "experiment", success=False)

    result = policy.conclude_test(proposal.id)
    assert result["winner"] == "control"


def test_concluded_proposal_status_updated(
    policy, large_improvement, current_weights
):
    """Concluded test has status='concluded' on disk."""
    proposal = policy.create_proposal(
        [large_improvement], current_weights, "status test"
    )
    for _ in range(5):
        policy.record_outcome(proposal.id, "control", success=True)
        policy.record_outcome(proposal.id, "experiment", success=True)

    policy.conclude_test(proposal.id)

    loaded = policy._load_proposals(status="concluded")
    assert len(loaded) == 1
    assert loaded[0].status == "concluded"


# ═══════════════════════════════════════════════════════════════
# GET ACTIVE PROPOSAL
# ═══════════════════════════════════════════════════════════════


def test_get_active_proposal_none(policy):
    """Returns None when no proposals are running."""
    assert policy.get_active_proposal() is None


def test_get_active_proposal_returns_running(
    policy, large_improvement, current_weights
):
    """Returns the running proposal."""
    proposal = policy.create_proposal(
        [large_improvement], current_weights, "active test"
    )
    active = policy.get_active_proposal()
    assert active is not None
    assert active.id == proposal.id


# ═══════════════════════════════════════════════════════════════
# DAEMON INTEGRATION
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def mock_daemon_deps():
    """Mock all daemon dependencies so run_cycle can execute."""
    with patch("core.evolution.daemon.MetricsCollector") as mock_collector, \
         patch("core.evolution.daemon.EvolutionAnalyzer") as mock_analyzer, \
         patch("core.evolution.daemon.ChangeDeployer") as mock_deployer, \
         patch("core.evolution.daemon.run_from_evals", new_callable=AsyncMock) as mock_auto:

        # Collector returns enough interactions
        collector_inst = mock_collector.return_value
        collector_inst.collect.return_value = {
            "failures_by_tier": [{"total": 50, "selected_tier": 1}],
        }

        # Analyzer returns a recommendation that will produce a large improvement
        from core.evolution.analyzer import AnalysisResult

        analyzer_inst = mock_analyzer.return_value
        analyzer_inst.analyze = AsyncMock(return_value=AnalysisResult(
            problems=[{"type": "under_routing", "severity": "medium"}],
            recommendations=[{
                "type": "weight_adjustment",
                "target": "requires_code_weight",
                "proposed": 0.30,
                "reason": "test recommendation",
            }],
            confidence=0.7,
            analysis_source="rule_based",
        ))

        # Deployer succeeds
        from core.evolution.deployer import DeployResult

        deployer_inst = mock_deployer.return_value
        deployer_inst.deploy.return_value = DeployResult(
            success=True, version=3, changes_applied=1
        )

        # Auto-improve is a no-op
        mock_auto_report = MagicMock()
        mock_auto_report.actions_proposed = 0
        mock_auto.return_value = mock_auto_report

        yield {
            "collector": collector_inst,
            "analyzer": analyzer_inst,
            "deployer": deployer_inst,
            "auto_improve": mock_auto,
        }


def test_daemon_without_split_policy_deploys_normally(
    mock_daemon_deps, current_weights, tmp_path
):
    """Backward compat: daemon without split_policy deploys directly."""
    from core.evolution.daemon import EvolutionDaemon, EvolutionConfig

    config = EvolutionConfig(
        auto_deploy=True,
        require_validation=False,
        min_interactions_for_cycle=5,
        cycle_log_dir=str(tmp_path / "cycles"),
        weights_path=str(tmp_path / "weights.yaml"),
    )

    # Write a weights file
    import yaml
    weights_path = tmp_path / "weights.yaml"
    weights_path.write_text(yaml.dump(current_weights))

    daemon = EvolutionDaemon(config=config, split_policy=None)

    result = asyncio.run(daemon.run_cycle())

    # Should have deployed
    mock_daemon_deps["deployer"].deploy.assert_called_once()


def test_daemon_with_split_policy_creates_proposal(
    mock_daemon_deps, current_weights, tmp_path
):
    """Daemon with split_policy creates proposal instead of deploying
    when changes are large enough to trigger split testing."""
    from core.evolution.daemon import EvolutionDaemon, EvolutionConfig

    proposals_dir = str(tmp_path / "proposals")
    split_policy = EvolutionSplitTestPolicy(
        min_samples=30,
        split_test_threshold=0.10,
        proposals_dir=proposals_dir,
    )

    config = EvolutionConfig(
        auto_deploy=True,
        require_validation=False,
        min_interactions_for_cycle=5,
        cycle_log_dir=str(tmp_path / "cycles"),
        weights_path=str(tmp_path / "weights.yaml"),
    )

    # Write a weights file with values that will produce > 10% change
    import yaml
    weights_path = tmp_path / "weights.yaml"
    weights_path.write_text(yaml.dump(current_weights))

    daemon = EvolutionDaemon(config=config, split_policy=split_policy)

    result = asyncio.run(daemon.run_cycle())

    # Should NOT have deployed (split test gate caught it)
    mock_daemon_deps["deployer"].deploy.assert_not_called()

    # Should have created a proposal on disk
    proposal_files = list(Path(proposals_dir).glob("*.json"))
    assert len(proposal_files) >= 1


# ═══════════════════════════════════════════════════════════════
# EDGE CASES
# ═══════════════════════════════════════════════════════════════


def test_conclude_nonexistent_proposal(policy):
    """Concluding a non-existent proposal returns error dict."""
    result = policy.conclude_test("does_not_exist")
    assert "error" in result


def test_load_empty_proposals_dir(policy):
    """Loading from empty dir returns empty list."""
    proposals = policy._load_proposals()
    assert proposals == []


def test_multiple_proposals_only_one_running(
    policy, large_improvement, current_weights
):
    """get_active_proposal returns the first running proposal."""
    p1 = policy.create_proposal(
        [large_improvement], current_weights, "first"
    )

    # Conclude the first
    for _ in range(5):
        policy.record_outcome(p1.id, "control", success=True)
        policy.record_outcome(p1.id, "experiment", success=True)
    policy.conclude_test(p1.id)

    # Create a second
    p2 = policy.create_proposal(
        [large_improvement], current_weights, "second"
    )

    active = policy.get_active_proposal()
    assert active is not None
    assert active.id == p2.id
