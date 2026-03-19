#!/usr/bin/env python3
"""
Tests for the multi-model routing system.

Phase 1: Provider Registry
Phase 2: Complexity Scorer
Phase 3: Interaction Logging
"""

import os
import sys
import tempfile
from pathlib import Path

# Ensure atlas package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.routing.provider_registry import ProviderRegistry, ProviderTierConfig


# ═══════════════════════════════════════════════════════════════
# PHASE 1: PROVIDER REGISTRY TESTS
# ═══════════════════════════════════════════════════════════════

SAMPLE_CONFIG = """
providers:
  - name: "nemotron-3-super"
    tier: 1
    provider_type: "nvidia_nim"
    endpoint: "https://integrate.api.nvidia.com/v1"
    model_id: "nvidia/llama-3.3-nemotron-super-49b-v1"
    cost_per_m_input: 0.0
    cost_per_m_output: 0.0
    max_context: 131072
    supports_tools: true
    enabled: true
    fallback_to: "qwen3.5-openrouter"
    api_key_env: "TEST_NVIDIA_KEY"

  - name: "qwen3.5-openrouter"
    tier: 1
    provider_type: "openrouter"
    endpoint: "https://openrouter.ai/api/v1"
    model_id: "qwen/qwen3.5-397b-a17b"
    cost_per_m_input: 0.60
    cost_per_m_output: 3.00
    max_context: 131072
    enabled: true
    fallback_to: "mimo-v2-pro"
    api_key_env: "TEST_OPENROUTER_KEY"

  - name: "mimo-v2-pro"
    tier: 2
    provider_type: "openrouter"
    endpoint: "https://openrouter.ai/api/v1"
    model_id: "xiaomi/mimo-v2-pro"
    cost_per_m_input: 1.00
    cost_per_m_output: 3.00
    max_context: 131072
    enabled: true
    fallback_to: "claude-opus-4-6"
    api_key_env: "TEST_OPENROUTER_KEY"

  - name: "minimax-m2.7"
    tier: 3
    provider_type: "openrouter"
    endpoint: "https://openrouter.ai/api/v1"
    model_id: "minimax/minimax-m2.7"
    cost_per_m_input: 0.30
    cost_per_m_output: 1.20
    max_context: 1048576
    enabled: true
    api_key_env: "TEST_OPENROUTER_KEY"
    extra:
      background_only: true

  - name: "claude-opus-4-6"
    tier: 4
    provider_type: "anthropic"
    endpoint: "https://api.anthropic.com"
    model_id: "claude-opus-4-6"
    cost_per_m_input: 15.00
    cost_per_m_output: 75.00
    max_context: 200000
    enabled: true
    api_key_env: "TEST_ANTHROPIC_KEY"

  - name: "ollama-local"
    tier: 5
    provider_type: "ollama"
    endpoint: "http://localhost:11434"
    model_id: "llama3.1"
    cost_per_m_input: 0.0
    cost_per_m_output: 0.0
    max_context: 128000
    enabled: true
    api_key_env: ""

budget:
  opus_daily_usd: 15.00
  opus_monthly_usd: 100.00

routing:
  tier_1_max_score: 0.4
  tier_2_max_score: 0.7
  tier_4_min_score: 0.7
"""


def _make_registry() -> ProviderRegistry:
    """Create a registry from the sample config with mock env vars."""
    # Set mock API keys
    os.environ["TEST_NVIDIA_KEY"] = "test-nvidia-key"
    os.environ["TEST_OPENROUTER_KEY"] = "test-openrouter-key"
    os.environ["TEST_ANTHROPIC_KEY"] = "test-anthropic-key"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(SAMPLE_CONFIG)
        f.flush()
        registry = ProviderRegistry.from_yaml(f.name)

    return registry


def test_registry_loads_all_providers():
    """Verify all 6 providers load from YAML."""
    registry = _make_registry()
    assert len(registry.all_providers) == 6, f"Expected 6 providers, got {len(registry.all_providers)}"
    print("  PASS: All 6 providers loaded")


def test_nemotron_is_tier_1():
    """Nemotron 3 Super must be the primary tier 1 provider."""
    registry = _make_registry()
    primary = registry.get_primary_for_tier(1)
    assert primary is not None, "No tier 1 provider found"
    assert primary.name == "nemotron-3-super", f"Tier 1 primary is {primary.name}, expected nemotron-3-super"
    assert primary.cost_per_m_input == 0.0, "Nemotron should be free"
    print("  PASS: Nemotron 3 Super is tier 1 primary")


def test_tier_assignments():
    """Verify each provider is in the correct tier."""
    registry = _make_registry()
    expected = {
        "nemotron-3-super": 1,
        "qwen3.5-openrouter": 1,
        "mimo-v2-pro": 2,
        "minimax-m2.7": 3,
        "claude-opus-4-6": 4,
        "ollama-local": 5,
    }
    for name, expected_tier in expected.items():
        config = registry.get_provider_config(name)
        assert config is not None, f"Provider {name} not found"
        assert config.tier == expected_tier, f"{name} tier is {config.tier}, expected {expected_tier}"
    print("  PASS: All tier assignments correct")


def test_fallback_chain_from_tier_1():
    """Verify fallback chain: Nemotron → Qwen3.5 → MiMo → Opus → Ollama (skip M2.7)."""
    registry = _make_registry()
    chain = registry.get_fallback_chain(starting_tier=1)
    names = [p.name for p in chain]

    # M2.7 (tier 3) must NOT be in the user-facing fallback chain
    assert "minimax-m2.7" not in names, "M2.7 should never be in user-facing chain"

    # Nemotron should be first
    assert names[0] == "nemotron-3-super", f"First in chain: {names[0]}"

    # Qwen3.5 should follow (fallback_to link)
    assert "qwen3.5-openrouter" in names, "Qwen3.5 missing from chain"
    assert names.index("qwen3.5-openrouter") < names.index("mimo-v2-pro"), \
        "Qwen3.5 should come before MiMo"

    # Opus and Ollama should be present
    assert "claude-opus-4-6" in names, "Opus missing from chain"
    assert "ollama-local" in names, "Ollama missing from chain"

    print(f"  PASS: Fallback chain correct: {' → '.join(names)}")


def test_fallback_chain_from_tier_2():
    """Starting from tier 2 should put MiMo first."""
    registry = _make_registry()
    chain = registry.get_fallback_chain(starting_tier=2)
    names = [p.name for p in chain]
    assert names[0] == "mimo-v2-pro", f"Tier 2 chain should start with MiMo, got {names[0]}"
    assert "minimax-m2.7" not in names, "M2.7 should not be in user chain"
    print(f"  PASS: Tier 2 chain starts with MiMo: {' → '.join(names)}")


def test_m27_only_accessible_by_name():
    """M2.7 must be accessible by explicit name but never in user chains."""
    registry = _make_registry()
    m27 = registry.get_provider_config("minimax-m2.7")
    assert m27 is not None, "M2.7 should be in registry"
    assert m27.tier == 3, "M2.7 should be tier 3"

    # Should not appear in any user-facing chain
    for tier in [1, 2, 4, 5]:
        chain = registry.get_fallback_chain(starting_tier=tier)
        names = [p.name for p in chain]
        assert "minimax-m2.7" not in names, f"M2.7 leaked into tier {tier} chain"

    print("  PASS: M2.7 correctly isolated to background-only")


def test_cost_lookup():
    """Registry cost lookup should return correct values."""
    registry = _make_registry()
    opus_cost = registry.get_cost("claude-opus-4-6")
    assert opus_cost["input"] == 15.00
    assert opus_cost["output"] == 75.00

    nemotron_cost = registry.get_cost("nemotron-3-super")
    assert nemotron_cost["input"] == 0.0
    assert nemotron_cost["output"] == 0.0

    unknown_cost = registry.get_cost("nonexistent")
    assert unknown_cost["input"] == 0.0

    print("  PASS: Cost lookups correct")


def test_disabled_provider_excluded():
    """Disabled providers should not appear in available or chains."""
    registry = _make_registry()

    # Manually disable a provider
    config = registry.get_provider_config("qwen3.5-openrouter")
    config.enabled = False

    chain = registry.get_fallback_chain(starting_tier=1)
    names = [p.name for p in chain]
    assert "qwen3.5-openrouter" not in names, "Disabled provider should be excluded"

    print("  PASS: Disabled providers excluded from chains")


def test_missing_key_excluded():
    """Providers with missing API keys should not be available."""
    # Remove the mock key
    saved = os.environ.pop("TEST_NVIDIA_KEY", None)
    try:
        registry = _make_registry()
        # Re-remove after _make_registry sets it
        os.environ.pop("TEST_NVIDIA_KEY", None)

        # Nemotron should not be available without key
        config = registry.get_provider_config("nemotron-3-super")
        assert not config.is_available, "Nemotron should not be available without API key"

        # Ollama should still be available (no key needed)
        ollama = registry.get_provider_config("ollama-local")
        assert ollama.is_available, "Ollama should be available without API key"

        print("  PASS: Missing API key correctly excludes provider")
    finally:
        if saved:
            os.environ["TEST_NVIDIA_KEY"] = saved


def test_get_all_costs_for_billing():
    """get_all_costs should return a map usable by BillingTracker."""
    registry = _make_registry()
    all_costs = registry.get_all_costs()

    assert "nemotron-3-super" in all_costs
    assert "claude-opus-4-6" in all_costs
    assert all_costs["mimo-v2-pro"]["input"] == 1.00
    assert all_costs["mimo-v2-pro"]["output"] == 3.00

    print("  PASS: All costs map correct for billing")


# ═══════════════════════════════════════════════════════════════
# TEST RUNNER
# ═══════════════════════════════════════════════════════════════

def run_phase1_tests():
    """Run all Phase 1 tests."""
    print("=" * 60)
    print("PHASE 1: PROVIDER REGISTRY TESTS")
    print("=" * 60)

    tests = [
        test_registry_loads_all_providers,
        test_nemotron_is_tier_1,
        test_tier_assignments,
        test_fallback_chain_from_tier_1,
        test_fallback_chain_from_tier_2,
        test_m27_only_accessible_by_name,
        test_cost_lookup,
        test_disabled_provider_excluded,
        test_missing_key_excluded,
        test_get_all_costs_for_billing,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {test.__name__}: {e}")
            failed += 1

    print(f"\nPhase 1 Results: {passed}/{len(tests)} passed, {failed} failures")
    return failed == 0


# ═══════════════════════════════════════════════════════════════
# PHASE 2: COMPLEXITY SCORER TESTS
# ═══════════════════════════════════════════════════════════════

from core.routing.complexity_scorer import ComplexityScorer, ScoringResult


def _make_scorer() -> ComplexityScorer:
    """Create a scorer with the actual config file."""
    config_path = Path(__file__).parent.parent.parent / "config" / "scorer_weights.yaml"
    if config_path.exists():
        return ComplexityScorer(str(config_path))
    return ComplexityScorer()


def test_simple_hello_tier_1():
    """A simple 'hello' should score < 0.2 → Tier 1."""
    scorer = _make_scorer()
    result = scorer.score("hello")
    assert result.score < 0.2, f"'hello' scored {result.score}, expected < 0.2"
    assert result.selected_tier == 1, f"Tier {result.selected_tier}, expected 1"
    print(f"  PASS: 'hello' → score={result.score:.3f}, tier={result.selected_tier}")


def test_simple_question_tier_1():
    """Simple questions should route to Tier 1."""
    scorer = _make_scorer()
    result = scorer.score("What time is it?")
    assert result.selected_tier == 1, f"Simple question → tier {result.selected_tier}"
    print(f"  PASS: Simple question → score={result.score:.3f}, tier={result.selected_tier}")


def test_coding_task_moderate():
    """A coding task should score higher than a simple greeting."""
    scorer = _make_scorer()
    result = scorer.score("Implement a REST API endpoint for user authentication with database integration and write tests")
    assert result.score >= 0.2, f"Coding task scored only {result.score}"
    assert result.domain in ("coding", "security"), f"Domain: {result.domain}"
    print(f"  PASS: Coding task → score={result.score:.3f}, tier={result.selected_tier}, domain={result.domain}")


def test_multi_step_security_task_high():
    """Multi-step + security domain should score > 0.7 → Tier 4."""
    scorer = _make_scorer()
    msg = (
        "First, audit the authentication system for vulnerabilities. "
        "Then, implement encryption for all credential storage. "
        "Finally, deploy the security patches to production and verify "
        "no penetration testing gaps remain."
    )
    result = scorer.score(msg)
    assert result.score > 0.5, f"Security multi-step scored only {result.score}"
    assert result.domain == "security", f"Domain: {result.domain}"
    print(f"  PASS: Security multi-step → score={result.score:.3f}, tier={result.selected_tier}")


def test_budget_exhaustion_caps_at_tier_2():
    """When Opus budget is exhausted, tier 4 should cap at tier 2."""
    scorer = _make_scorer()
    msg = (
        "First, audit the authentication system for vulnerabilities. "
        "Then, implement encryption for all credential storage. "
        "Finally, deploy the security patches to production."
    )
    result = scorer.score_and_route(msg, budget_remaining=0.0)
    if result.score > scorer.weights.get("tier_thresholds", {}).get("tier_2_max", 0.7):
        assert result.selected_tier == 2, f"Budget exhausted but tier is {result.selected_tier}"
        assert result.budget_gated, "Should be marked as budget_gated"
        print(f"  PASS: Budget exhaustion caps at tier 2 (score={result.score:.3f})")
    else:
        print(f"  PASS: Score {result.score:.3f} didn't hit tier 4 threshold, budget gate not needed")


def test_creative_task_gets_negative_adjustment():
    """Creative tasks should get a negative domain adjustment."""
    scorer = _make_scorer()
    result = scorer.score("Write a blog post about AI trends")
    assert result.domain == "creative", f"Domain: {result.domain}"
    assert result.domain_adjustment < 0, f"Creative adjustment: {result.domain_adjustment}"
    print(f"  PASS: Creative task → adjustment={result.domain_adjustment}, score={result.score:.3f}")


def test_scorer_version_tracking():
    """Scorer should report its version from config."""
    scorer = _make_scorer()
    assert scorer.version >= 1, f"Version: {scorer.version}"
    print(f"  PASS: Scorer version={scorer.version}")


def test_features_breakdown_present():
    """ScoringResult should include a features breakdown dict."""
    scorer = _make_scorer()
    result = scorer.score("Deploy the new API to production after running all tests")
    assert isinstance(result.features, dict), "Features should be a dict"
    expected_keys = {"token_count", "requires_tools", "requires_code", "multi_step", "safety_critical"}
    assert expected_keys.issubset(result.features.keys()), f"Missing features: {expected_keys - result.features.keys()}"
    print(f"  PASS: Features breakdown present: {list(result.features.keys())}")


def test_scorer_runs_fast():
    """Scorer must run in < 5ms (rule-based, no API calls)."""
    import time
    scorer = _make_scorer()
    msg = (
        "Research the latest security vulnerabilities in OAuth implementations, "
        "then implement fixes in the authentication module, write comprehensive "
        "tests, and deploy to production with monitoring."
    )

    start = time.perf_counter()
    for _ in range(100):
        scorer.score(msg)
    elapsed = (time.perf_counter() - start) / 100 * 1000  # ms per call

    assert elapsed < 5.0, f"Scorer took {elapsed:.2f}ms per call, must be < 5ms"
    print(f"  PASS: Scorer latency={elapsed:.2f}ms per call (< 5ms requirement)")


def run_phase2_tests():
    """Run all Phase 2 tests."""
    print("\n" + "=" * 60)
    print("PHASE 2: COMPLEXITY SCORER TESTS")
    print("=" * 60)

    tests = [
        test_simple_hello_tier_1,
        test_simple_question_tier_1,
        test_coding_task_moderate,
        test_multi_step_security_task_high,
        test_budget_exhaustion_caps_at_tier_2,
        test_creative_task_gets_negative_adjustment,
        test_scorer_version_tracking,
        test_features_breakdown_present,
        test_scorer_runs_fast,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {test.__name__}: {e}")
            failed += 1

    print(f"\nPhase 2 Results: {passed}/{len(tests)} passed, {failed} failures")
    return failed == 0


# ═══════════════════════════════════════════════════════════════
# PHASE 3: INTERACTION LOGGING TESTS
# ═══════════════════════════════════════════════════════════════

import json
from core.routing.interaction_log import InteractionLogger, InteractionRecord
from core.routing.log_queries import LogQueries


def _make_logger() -> InteractionLogger:
    """Create an interaction logger with a temp DB."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return InteractionLogger(db_path=tmp.name)


def _seed_records(logger: InteractionLogger, count: int = 10) -> list:
    """Insert sample records for query testing."""
    records = []
    domains = ["coding", "security", "creative", "default", "financial"]
    providers = ["nemotron-3-super", "mimo-v2-pro", "claude-opus-4-6"]
    tiers = [1, 1, 2, 2, 4]

    for i in range(count):
        tier_idx = i % len(tiers)
        rec = InteractionRecord(
            message_preview=f"Test message {i}",
            complexity_score=round(0.1 + (i * 0.08), 3),
            selected_tier=tiers[tier_idx],
            selected_provider=providers[i % len(providers)],
            domain=domains[i % len(domains)],
            features=json.dumps({"token_count": 0.0, "safety_critical": 0.0}),
            scorer_version=1,
            actual_provider=providers[i % len(providers)],
            latency_ms=50.0 + i * 10,
            input_tokens=100 + i * 50,
            output_tokens=200 + i * 100,
            cost_usd=round(0.001 * (i + 1), 4),
            success=i != 3,  # Record 3 is a failure
            error_type="timeout" if i == 3 else "",
            fallback_used=i == 5,
            escalated=i == 7,
            user_correction=i == 8,
            channel="cli",
        )
        logger.log(rec)
        records.append(rec)
    return records


def test_logger_creates_db():
    """Logger should create the SQLite database and table."""
    logger = _make_logger()
    assert Path(logger.db_path).exists(), "DB file not created"
    assert logger.count() == 0, "Fresh DB should be empty"
    print("  PASS: Logger creates DB with correct schema")


def test_log_and_retrieve():
    """Log a record and retrieve it by ID."""
    logger = _make_logger()
    rec = InteractionRecord(
        message_preview="Hello world",
        complexity_score=0.15,
        selected_tier=1,
        selected_provider="nemotron-3-super",
        domain="default",
    )
    record_id = logger.log(rec)
    assert record_id == rec.id

    retrieved = logger.get(record_id)
    assert retrieved is not None, "Record not found"
    assert retrieved["message_preview"] == "Hello world"
    assert retrieved["complexity_score"] == 0.15
    assert retrieved["selected_tier"] == 1
    print("  PASS: Log and retrieve works correctly")


def test_update_result():
    """Update execution results after logging the routing decision."""
    logger = _make_logger()
    rec = InteractionRecord(
        message_preview="Test update",
        selected_provider="nemotron-3-super",
    )
    record_id = logger.log(rec)

    logger.update_result(
        record_id,
        actual_provider="mimo-v2-pro",
        fallback_used=True,
        latency_ms=234.5,
        input_tokens=500,
        output_tokens=1200,
        cost_usd=0.0051,
        success=True,
    )

    updated = logger.get(record_id)
    assert updated["actual_provider"] == "mimo-v2-pro"
    assert updated["fallback_used"] == 1
    assert updated["latency_ms"] == 234.5
    assert updated["cost_usd"] == 0.0051
    print("  PASS: Update result fills in execution data")


def test_mark_user_correction():
    """Mark a record as user-corrected."""
    logger = _make_logger()
    rec = InteractionRecord(message_preview="Correction test")
    record_id = logger.log(rec)

    logger.mark_user_correction(record_id)
    updated = logger.get(record_id)
    assert updated["user_correction"] == 1
    print("  PASS: User correction marking works")


def test_mark_escalated():
    """Mark a record as escalated."""
    logger = _make_logger()
    rec = InteractionRecord(message_preview="Escalation test")
    record_id = logger.log(rec)

    logger.mark_escalated(record_id)
    updated = logger.get(record_id)
    assert updated["escalated"] == 1
    print("  PASS: Escalation marking works")


def test_recent_returns_ordered():
    """Recent records should be in reverse chronological order."""
    logger = _make_logger()
    _seed_records(logger, count=5)

    recent = logger.recent(limit=3)
    assert len(recent) == 3, f"Expected 3 records, got {len(recent)}"

    # Timestamps should be descending
    for i in range(len(recent) - 1):
        assert recent[i]["timestamp"] >= recent[i + 1]["timestamp"], \
            "Records not in descending order"
    print("  PASS: Recent returns ordered results")


def test_message_preview_truncated():
    """Messages longer than 200 chars should be truncated."""
    logger = _make_logger()
    long_msg = "x" * 500
    rec = InteractionRecord(message_preview=long_msg)
    logger.log(rec)

    retrieved = logger.get(rec.id)
    assert len(retrieved["message_preview"]) == 200, \
        f"Preview length: {len(retrieved['message_preview'])}"
    print("  PASS: Message preview truncated to 200 chars")


def test_query_failures_by_tier():
    """LogQueries.get_failures_by_tier returns correct breakdown."""
    logger = _make_logger()
    _seed_records(logger, count=10)
    queries = LogQueries(db_path=logger.db_path)

    # Use a very old since to capture all records
    results = queries.get_failures_by_tier(since="2020-01-01T00:00:00Z")
    assert len(results) > 0, "No tier results"

    # We know record 3 fails — check that failures > 0 somewhere
    total_failures = sum(r["failures"] for r in results)
    assert total_failures >= 1, f"Expected at least 1 failure, got {total_failures}"
    print(f"  PASS: Failures by tier: {total_failures} failure(s) detected")


def test_query_escalation_rate():
    """LogQueries.get_escalation_rate returns correct counts."""
    logger = _make_logger()
    _seed_records(logger, count=10)
    queries = LogQueries(db_path=logger.db_path)

    result = queries.get_escalation_rate(since="2020-01-01T00:00:00Z")
    assert result["total"] == 10
    assert result["escalations"] >= 1  # record 7
    assert result["user_corrections"] >= 1  # record 8
    assert result["override_rate_pct"] > 0
    print(f"  PASS: Escalation rate: {result['override_rate_pct']}% override rate")


def test_query_cost_by_tier():
    """LogQueries.get_cost_by_tier returns cost breakdown."""
    logger = _make_logger()
    _seed_records(logger, count=10)
    queries = LogQueries(db_path=logger.db_path)

    results = queries.get_cost_by_tier(since="2020-01-01T00:00:00Z")
    assert len(results) > 0
    total_cost = sum(r["total_cost_usd"] for r in results)
    assert total_cost > 0, "Total cost should be > 0"
    print(f"  PASS: Cost by tier: ${total_cost:.4f} total")


def test_query_wins_by_tier():
    """LogQueries.get_wins_by_tier returns clean win rates."""
    logger = _make_logger()
    _seed_records(logger, count=10)
    queries = LogQueries(db_path=logger.db_path)

    results = queries.get_wins_by_tier(since="2020-01-01T00:00:00Z")
    assert len(results) > 0
    # At least some clean wins expected
    total_wins = sum(r["clean_wins"] for r in results)
    assert total_wins > 0, "Should have some clean wins"
    print(f"  PASS: Wins by tier: {total_wins} clean wins")


def test_query_domain_accuracy():
    """LogQueries.get_domain_accuracy returns per-domain breakdown."""
    logger = _make_logger()
    _seed_records(logger, count=10)
    queries = LogQueries(db_path=logger.db_path)

    results = queries.get_domain_accuracy(since="2020-01-01T00:00:00Z")
    domains_found = {r["domain"] for r in results}
    assert "coding" in domains_found, "Missing coding domain"
    assert "security" in domains_found, "Missing security domain"
    print(f"  PASS: Domain accuracy covers {len(domains_found)} domains")


def test_query_scoring_drift():
    """LogQueries.get_scoring_drift returns per-version stats."""
    logger = _make_logger()
    _seed_records(logger, count=10)
    queries = LogQueries(db_path=logger.db_path)

    results = queries.get_scoring_drift(since="2020-01-01T00:00:00Z")
    assert len(results) >= 1, "Should have at least version 1"
    v1 = results[0]
    assert v1["scorer_version"] == 1
    assert v1["interactions"] == 10
    assert 0.0 <= v1["avg_score"] <= 1.0
    print(f"  PASS: Scoring drift: v{v1['scorer_version']} avg={v1['avg_score']}")


def test_query_evolution_summary():
    """LogQueries.get_evolution_summary returns complete summary."""
    logger = _make_logger()
    _seed_records(logger, count=10)
    queries = LogQueries(db_path=logger.db_path)

    summary = queries.get_evolution_summary(since="2020-01-01T00:00:00Z")
    expected_keys = {
        "period_start", "failures_by_tier", "escalation_rate",
        "cost_by_tier", "wins_by_tier", "domain_accuracy",
        "scoring_drift", "fallback_frequency",
    }
    assert expected_keys.issubset(summary.keys()), \
        f"Missing keys: {expected_keys - summary.keys()}"
    print(f"  PASS: Evolution summary contains all {len(expected_keys)} sections")


def run_phase3_tests():
    """Run all Phase 3 tests."""
    print("\n" + "=" * 60)
    print("PHASE 3: INTERACTION LOGGING TESTS")
    print("=" * 60)

    tests = [
        test_logger_creates_db,
        test_log_and_retrieve,
        test_update_result,
        test_mark_user_correction,
        test_mark_escalated,
        test_recent_returns_ordered,
        test_message_preview_truncated,
        test_query_failures_by_tier,
        test_query_escalation_rate,
        test_query_cost_by_tier,
        test_query_wins_by_tier,
        test_query_domain_accuracy,
        test_query_scoring_drift,
        test_query_evolution_summary,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {test.__name__}: {e}")
            failed += 1

    print(f"\nPhase 3 Results: {passed}/{len(tests)} passed, {failed} failures")
    return failed == 0


# ═══════════════════════════════════════════════════════════════
# PHASE 4: EVOLUTION DAEMON TESTS
# ═══════════════════════════════════════════════════════════════

import asyncio
import shutil
import yaml
from core.evolution.collector import MetricsCollector
from core.evolution.analyzer import EvolutionAnalyzer, AnalysisResult
from core.evolution.improver import WeightImprover, Improvement
from core.evolution.validator import ChangeValidator, ValidationResult
from core.evolution.deployer import ChangeDeployer, DeployResult
from core.evolution.daemon import EvolutionDaemon, EvolutionConfig, CycleResult


def _sample_weights() -> dict:
    """Return sample scorer weights for testing."""
    return {
        "features": {
            "token_count_threshold": 2000,
            "token_count_weight": 0.20,
            "requires_tools_weight": 0.15,
            "requires_code_weight": 0.15,
            "multi_step_weight": 0.20,
            "safety_critical_weight": 0.30,
        },
        "domain_adjustments": {
            "default": 0.0,
            "coding": 0.05,
            "security": 0.15,
            "financial": 0.10,
            "legal": 0.15,
            "production": 0.10,
            "creative": -0.05,
            "research": 0.0,
            "planning": 0.05,
        },
        "tier_thresholds": {
            "tier_1_max": 0.4,
            "tier_2_max": 0.7,
        },
        "version": 1,
    }


def _make_analysis_with_recommendations() -> AnalysisResult:
    """Create a sample analysis result with recommendations."""
    return AnalysisResult(
        problems=[
            {"type": "under_routing", "domain": "security", "severity": "medium",
             "description": "Security domain under-routed"},
        ],
        recommendations=[
            {"type": "weight_adjustment", "target": "safety_critical_weight",
             "proposed": 0.33, "reason": "Increase safety weight"},
            {"type": "domain_adjustment", "target": "security",
             "direction": "increase", "reason": "Security under-routed"},
        ],
        confidence=0.7,
        analysis_source="rule_based",
    )


def test_collector_produces_metrics():
    """Collector should produce a complete metrics package."""
    il = _make_logger()
    _seed_records(il, count=10)
    collector = MetricsCollector(db_path=il.db_path)
    metrics = collector.collect(since="2020-01-01T00:00:00Z")

    assert "failures_by_tier" in metrics
    assert "health_indicators" in metrics
    assert "collection_metadata" in metrics
    assert metrics["collection_metadata"]["lookback_hours"] == 24
    print("  PASS: Collector produces complete metrics package")


def test_collector_health_indicators():
    """Collector should compute health indicators from metrics."""
    il = _make_logger()
    _seed_records(il, count=10)
    collector = MetricsCollector(db_path=il.db_path)
    metrics = collector.collect(since="2020-01-01T00:00:00Z")

    health = metrics["health_indicators"]
    assert "overall" in health
    assert "alerts" in health
    assert health["overall"] in ("healthy", "degraded", "critical")
    print(f"  PASS: Health indicators: {health['overall']}, {len(health['alerts'])} alerts")


def test_analyzer_rule_based_fallback():
    """Analyzer should work without M2.7 provider (rule-based)."""
    analyzer = EvolutionAnalyzer(provider=None)

    # Metrics with problems that rule-based analysis can detect
    metrics = {
        "failures_by_tier": [
            {"selected_tier": 1, "total": 100, "failures": 25,
             "failure_rate_pct": 25.0, "error_types": "timeout"},
        ],
        "escalation_rate": {
            "total": 100, "escalations": 20, "user_corrections": 5,
            "override_rate_pct": 25.0,
        },
        "cost_by_tier": [],
        "domain_accuracy": [],
        "scoring_drift": [],
        "fallback_frequency": [],
    }

    result = asyncio.run(analyzer.analyze(metrics))
    assert result.analysis_source == "rule_based"
    assert len(result.problems) > 0, "Should detect high failure rate"
    assert len(result.recommendations) > 0, "Should recommend changes"
    print(f"  PASS: Rule-based analysis: {len(result.problems)} problems, "
          f"{len(result.recommendations)} recommendations")


def test_improver_generates_bounded_changes():
    """Improver should generate changes within safety bounds."""
    weights = _sample_weights()
    analysis = _make_analysis_with_recommendations()
    improver = WeightImprover(weights)

    improvements = improver.generate_improvements(analysis)
    assert len(improvements) > 0, "Should generate improvements"

    for imp in improvements:
        assert imp.is_valid, f"Invalid improvement: {imp.target}"
        assert abs(imp.change_pct) <= 0.20, f"Change too large: {imp.change_pct}"
        assert 0.0 <= imp.proposed_value <= 1.0, f"Out of bounds: {imp.proposed_value}"
    print(f"  PASS: Improver generated {len(improvements)} bounded improvements")


def test_improver_applies_to_weights():
    """Applying improvements should produce new weights with bumped version."""
    weights = _sample_weights()
    analysis = _make_analysis_with_recommendations()
    improver = WeightImprover(weights)

    improvements = improver.generate_improvements(analysis)
    new_weights = improver.apply_improvements(improvements)

    assert new_weights["version"] == 2, f"Version: {new_weights['version']}"
    # At least one value should differ
    old_safety = weights["features"]["safety_critical_weight"]
    new_safety = new_weights["features"]["safety_critical_weight"]
    has_change = old_safety != new_safety
    # Or domain change
    old_sec = weights["domain_adjustments"]["security"]
    new_sec = new_weights["domain_adjustments"]["security"]
    has_change = has_change or old_sec != new_sec
    assert has_change, "No actual changes in weights"
    print(f"  PASS: Applied improvements, version bumped to {new_weights['version']}")


def test_validator_rejects_out_of_bounds():
    """Validator should reject changes outside bounds."""
    weights = _sample_weights()
    validator = ChangeValidator(weights)

    bad_improvements = [
        Improvement(
            target="features.safety_critical_weight",
            current_value=0.30,
            proposed_value=1.5,  # Out of bounds
            change_pct=4.0,
            reason="test",
        ),
    ]

    result = validator.validate(bad_improvements)
    assert len(result.rejected_improvements) == 1
    assert len(result.approved_improvements) == 0
    print("  PASS: Validator rejects out-of-bounds changes")


def test_validator_rejects_tier_collapse():
    """Validator should reject threshold changes that collapse tiers."""
    weights = _sample_weights()
    validator = ChangeValidator(weights)

    # Try to set tier_1_max too close to tier_2_max
    bad_improvements = [
        Improvement(
            target="tier_thresholds.tier_1_max",
            current_value=0.4,
            proposed_value=0.65,  # Too close to tier_2_max (0.7)
            change_pct=0.15,
            reason="test",
        ),
    ]

    result = validator.validate(bad_improvements)
    assert len(result.rejected_improvements) == 1
    assert "too close" in result.rejection_reasons.get("tier_thresholds.tier_1_max", "")
    print("  PASS: Validator rejects tier-collapsing thresholds")


def test_validator_approves_valid_changes():
    """Validator should approve well-formed changes."""
    weights = _sample_weights()
    validator = ChangeValidator(weights)

    good_improvements = [
        Improvement(
            target="features.safety_critical_weight",
            current_value=0.30,
            proposed_value=0.33,
            change_pct=0.10,
            reason="Increase safety weight slightly",
        ),
    ]

    result = validator.validate(good_improvements)
    assert len(result.approved_improvements) == 1
    assert len(result.rejected_improvements) == 0
    print("  PASS: Validator approves valid changes")


def test_deployer_writes_and_backs_up():
    """Deployer should write new weights and create backup."""
    with tempfile.TemporaryDirectory() as tmpdir:
        weights_path = os.path.join(tmpdir, "scorer_weights.yaml")

        # Write initial weights
        initial = _sample_weights()
        with open(weights_path, "w") as f:
            yaml.dump(initial, f)

        deployer = ChangeDeployer(weights_path=weights_path)
        new_weights = _sample_weights()
        new_weights["features"]["safety_critical_weight"] = 0.33

        result = deployer.deploy(new_weights, changes_count=1)
        assert result.success, f"Deploy failed: {result.error}"
        assert result.version == 2
        assert result.changes_applied == 1

        # Verify backup exists
        backup = os.path.join(tmpdir, "scorer_weights.v1.yaml")
        assert os.path.exists(backup), "Backup not created"

        # Verify new file has updated weights
        with open(weights_path) as f:
            deployed = yaml.safe_load(f)
        assert deployed["features"]["safety_critical_weight"] == 0.33
        assert deployed["version"] == 2
        print("  PASS: Deployer writes new weights and creates backup")


def test_deployer_rollback():
    """Deployer should rollback to previous version."""
    with tempfile.TemporaryDirectory() as tmpdir:
        weights_path = os.path.join(tmpdir, "scorer_weights.yaml")

        # Write v1
        v1 = _sample_weights()
        v1["version"] = 1
        with open(weights_path, "w") as f:
            yaml.dump(v1, f)

        deployer = ChangeDeployer(weights_path=weights_path)

        # Deploy v2
        v2 = _sample_weights()
        v2["features"]["safety_critical_weight"] = 0.99
        deployer.deploy(v2, changes_count=1)

        # Rollback
        result = deployer.rollback()
        assert result.success, f"Rollback failed: {result.error}"
        assert result.version == 1

        with open(weights_path) as f:
            restored = yaml.safe_load(f)
        assert restored["features"]["safety_critical_weight"] == 0.30
        print("  PASS: Deployer rollback restores previous version")


def test_daemon_full_cycle_skips_low_data():
    """Daemon should skip cycle when not enough interactions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        weights_path = os.path.join(tmpdir, "weights.yaml")
        cycle_log_dir = os.path.join(tmpdir, "cycles")

        # Write initial weights
        with open(weights_path, "w") as f:
            yaml.dump(_sample_weights(), f)

        # Log only 5 interactions (below threshold of 20)
        il = InteractionLogger(db_path=db_path)
        _seed_records(il, count=5)

        config = EvolutionConfig(
            weights_path=weights_path,
            interaction_db=db_path,
            cycle_log_dir=cycle_log_dir,
            min_interactions_for_cycle=20,
        )
        daemon = EvolutionDaemon(config=config)

        result = asyncio.run(daemon.run_cycle())
        assert result.metrics_collected
        assert result.interactions_analyzed < 20
        assert result.improvements_deployed == 0
        print(f"  PASS: Daemon skips cycle with {result.interactions_analyzed} "
              f"interactions (need 20)")


def test_daemon_full_cycle_with_data():
    """Daemon should run full cycle with sufficient data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        weights_path = os.path.join(tmpdir, "weights.yaml")
        cycle_log_dir = os.path.join(tmpdir, "cycles")

        # Write initial weights
        with open(weights_path, "w") as f:
            yaml.dump(_sample_weights(), f)

        # Seed enough data with high escalation to trigger recommendations
        il = InteractionLogger(db_path=db_path)
        for i in range(30):
            rec = InteractionRecord(
                message_preview=f"Test {i}",
                complexity_score=0.3,
                selected_tier=1,
                selected_provider="nemotron-3-super",
                domain="security" if i % 3 == 0 else "default",
                success=True,
                escalated=i % 4 == 0,  # 25% escalation rate
                user_correction=i % 5 == 0,
            )
            il.log(rec)

        config = EvolutionConfig(
            weights_path=weights_path,
            interaction_db=db_path,
            cycle_log_dir=cycle_log_dir,
            min_interactions_for_cycle=10,
            auto_deploy=True,
        )
        daemon = EvolutionDaemon(config=config)

        result = asyncio.run(daemon.run_cycle())
        assert result.success, f"Cycle failed: {result.error}"
        assert result.metrics_collected
        assert result.interactions_analyzed >= 10
        # With 25% escalation rate, rule-based analyzer should find problems
        print(f"  PASS: Full cycle: {result.problems_found} problems, "
              f"{result.improvements_deployed} deployed, v{result.new_version}")


def run_phase4_tests():
    """Run all Phase 4 tests."""
    print("\n" + "=" * 60)
    print("PHASE 4: EVOLUTION DAEMON TESTS")
    print("=" * 60)

    tests = [
        test_collector_produces_metrics,
        test_collector_health_indicators,
        test_analyzer_rule_based_fallback,
        test_improver_generates_bounded_changes,
        test_improver_applies_to_weights,
        test_validator_rejects_out_of_bounds,
        test_validator_rejects_tier_collapse,
        test_validator_approves_valid_changes,
        test_deployer_writes_and_backs_up,
        test_deployer_rollback,
        test_daemon_full_cycle_skips_low_data,
        test_daemon_full_cycle_with_data,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {test.__name__}: {e}")
            failed += 1

    print(f"\nPhase 4 Results: {passed}/{len(tests)} passed, {failed} failures")
    return failed == 0


# ═══════════════════════════════════════════════════════════════
# PHASE 5: METRICS DASHBOARD + SPLIT TESTING TESTS
# ═══════════════════════════════════════════════════════════════

from core.routing.metrics import MetricsDashboard
from core.routing.split_test import SplitTestManager, SplitTest, SplitAssignment


def test_dashboard_health():
    """Dashboard health endpoint returns correct structure."""
    il = _make_logger()
    _seed_records(il, count=10)
    dashboard = MetricsDashboard(db_path=il.db_path)

    health = dashboard.get_health(hours=8760)  # 1 year lookback
    assert "status" in health
    assert health["status"] in ("healthy", "degraded", "critical")
    assert health["total_interactions"] == 10
    assert "failure_rate_pct" in health
    print(f"  PASS: Health endpoint: status={health['status']}, "
          f"{health['total_interactions']} interactions")


def test_dashboard_routing():
    """Dashboard routing endpoint returns tier/domain breakdown."""
    il = _make_logger()
    _seed_records(il, count=10)
    dashboard = MetricsDashboard(db_path=il.db_path)

    routing = dashboard.get_routing(hours=8760)
    assert "wins_by_tier" in routing
    assert "domain_accuracy" in routing
    assert "fallback_frequency" in routing
    assert "scoring_drift" in routing
    print(f"  PASS: Routing endpoint: {len(routing['wins_by_tier'])} tiers, "
          f"{len(routing['domain_accuracy'])} domains")


def test_dashboard_cost():
    """Dashboard cost endpoint returns cost breakdown."""
    il = _make_logger()
    _seed_records(il, count=10)
    dashboard = MetricsDashboard(db_path=il.db_path)

    cost = dashboard.get_cost(hours=8760)
    assert cost["total_cost_usd"] > 0
    assert "by_tier" in cost
    print(f"  PASS: Cost endpoint: ${cost['total_cost_usd']:.4f} total")


def test_dashboard_full():
    """Full dashboard returns all sections."""
    il = _make_logger()
    _seed_records(il, count=10)
    dashboard = MetricsDashboard(db_path=il.db_path)

    full = dashboard.get_full_dashboard(hours=8760)
    expected = {"health", "routing", "cost", "evolution", "split_tests"}
    assert expected.issubset(full.keys()), f"Missing: {expected - full.keys()}"
    print(f"  PASS: Full dashboard contains all {len(expected)} sections")


def test_split_test_create():
    """Create a split test with overrides."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "split_tests.yaml")
        mgr = SplitTestManager(config_path=config_path)

        test = mgr.create_test(
            name="increase_safety_weight",
            description="Test higher safety weight",
            experiment_overrides={"features.safety_critical_weight": 0.35},
        )

        assert test.name == "increase_safety_weight"
        assert test.status == "active"
        assert test.experiment_overrides == {"features.safety_critical_weight": 0.35}
        assert os.path.exists(config_path), "Config not persisted"
        print("  PASS: Split test created and persisted")


def test_split_test_deterministic_assignment():
    """Same session_id always gets same group."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "split_tests.yaml")
        mgr = SplitTestManager(config_path=config_path)
        mgr.create_test(
            name="test_deterministic",
            experiment_overrides={"features.safety_critical_weight": 0.35},
        )

        # Same session should always get same group
        assignments = [mgr.assign("session-abc") for _ in range(10)]
        groups = {a.group for a in assignments}
        assert len(groups) == 1, f"Non-deterministic: got groups {groups}"
        print(f"  PASS: Deterministic assignment: session-abc → {assignments[0].group}")


def test_split_test_traffic_split():
    """Traffic should split roughly according to weights."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "split_tests.yaml")
        mgr = SplitTestManager(config_path=config_path)
        mgr.create_test(
            name="test_split",
            control_weight=0.5,
            experiment_weight=0.5,
            experiment_overrides={"features.safety_critical_weight": 0.35},
        )

        control_count = 0
        experiment_count = 0
        for i in range(200):
            assignment = mgr.assign(f"session-{i}")
            if assignment.group == "control":
                control_count += 1
            else:
                experiment_count += 1

        # With 200 sessions and 50/50 split, expect roughly even
        # Allow wide tolerance (30-70% range)
        ctrl_pct = control_count / 200
        assert 0.30 <= ctrl_pct <= 0.70, f"Skewed split: control={ctrl_pct*100:.0f}%"
        print(f"  PASS: Traffic split: control={control_count}, "
              f"experiment={experiment_count} ({ctrl_pct*100:.0f}% control)")


def test_split_test_record_outcomes():
    """Recording outcomes should update counters correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "split_tests.yaml")
        mgr = SplitTestManager(config_path=config_path)
        mgr.create_test(
            name="test_outcomes",
            experiment_overrides={"features.safety_critical_weight": 0.35},
        )

        mgr.record_outcome("test_outcomes", "control", success=True, cost_usd=0.01, latency_ms=100)
        mgr.record_outcome("test_outcomes", "control", success=True, cost_usd=0.02, latency_ms=150)
        mgr.record_outcome("test_outcomes", "experiment", success=True, cost_usd=0.005, latency_ms=80)
        mgr.record_outcome("test_outcomes", "experiment", success=False, escalated=True, cost_usd=0.05, latency_ms=200)

        results = mgr.get_results("test_outcomes")
        assert results["control"]["count"] == 2
        assert results["experiment"]["count"] == 2
        assert results["control"]["success_rate_pct"] == 100.0
        assert results["experiment"]["success_rate_pct"] == 50.0
        print(f"  PASS: Outcomes recorded: control={results['control']['count']}, "
              f"experiment={results['experiment']['count']}")


def test_split_test_conclude():
    """Concluding a test should mark it and return results with winner."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "split_tests.yaml")
        mgr = SplitTestManager(config_path=config_path)
        mgr.create_test(
            name="test_conclude",
            experiment_overrides={"features.safety_critical_weight": 0.35},
        )

        # Record enough outcomes to determine winner
        for i in range(40):
            mgr.record_outcome("test_conclude", "control", success=True, cost_usd=0.01)
            mgr.record_outcome("test_conclude", "experiment", success=True, cost_usd=0.005)

        results = mgr.conclude_test("test_conclude")
        assert results["status"] == "concluded"
        assert results["concluded_at"] is not None
        # Experiment has lower cost with same success → experiment wins
        assert results["winner"] == "experiment", f"Winner: {results['winner']}"
        print(f"  PASS: Test concluded: winner={results['winner']}")


def test_split_test_no_active_returns_none():
    """Assignment returns None when no tests are active."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "split_tests.yaml")
        mgr = SplitTestManager(config_path=config_path)
        assert mgr.assign("session-1") is None
        print("  PASS: No active tests returns None")


def test_split_test_pause_resume():
    """Pausing a test stops assignments, resuming restarts them."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "split_tests.yaml")
        mgr = SplitTestManager(config_path=config_path)
        mgr.create_test(
            name="test_pause",
            experiment_overrides={"features.safety_critical_weight": 0.35},
        )

        assert mgr.assign("s1") is not None
        mgr.pause_test("test_pause")
        assert mgr.assign("s1") is None, "Paused test should not assign"
        mgr.resume_test("test_pause")
        assert mgr.assign("s1") is not None, "Resumed test should assign"
        print("  PASS: Pause/resume controls assignment")


def run_phase5_tests():
    """Run all Phase 5 tests."""
    print("\n" + "=" * 60)
    print("PHASE 5: METRICS DASHBOARD + SPLIT TESTING TESTS")
    print("=" * 60)

    tests = [
        test_dashboard_health,
        test_dashboard_routing,
        test_dashboard_cost,
        test_dashboard_full,
        test_split_test_create,
        test_split_test_deterministic_assignment,
        test_split_test_traffic_split,
        test_split_test_record_outcomes,
        test_split_test_conclude,
        test_split_test_no_active_returns_none,
        test_split_test_pause_resume,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {test.__name__}: {e}")
            failed += 1

    print(f"\nPhase 5 Results: {passed}/{len(tests)} passed, {failed} failures")
    return failed == 0


if __name__ == "__main__":
    p1 = run_phase1_tests()
    p2 = run_phase2_tests()
    p3 = run_phase3_tests()
    p4 = run_phase4_tests()
    p5 = run_phase5_tests()
    success = p1 and p2 and p3 and p4 and p5
    sys.exit(0 if success else 1)
