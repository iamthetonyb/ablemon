# Multi-Model Routing System

## Overview

ATLAS uses a 4-tier complexity-scored routing system that replaces the linear provider fallback chain with intelligent task-aware routing. An async self-evolution daemon continuously improves routing accuracy.

## Architecture

```
User Message → ComplexityScorer → ProviderRegistry → LLM Provider
                   │                    │
                   │                    └── Fallback chain (skip M2.7)
                   │
                   └── InteractionLogger → LogQueries
                                              │
                              EvolutionDaemon (M2.7, background-only)
                              │  Collect → Analyze → Improve → Validate → Deploy
                              └── scorer_weights.yaml (hot-reloaded)
```

## Provider Tiers

| Tier | Provider | Model | Cost (in/out per M) | Use Case |
|------|----------|-------|---------------------|----------|
| 1 | Nemotron 3 Super | nvidia/llama-3.3-nemotron-super-49b-v1 | $0/$0 | Default — simple tasks |
| 1 | Qwen3.5 (legacy) | qwen/qwen3.5-397b-a17b | $0.60/$3.00 | Fallback for Nemotron |
| 2 | MiMo-V2-Pro | xiaomi/mimo-v2-pro | $1.00/$3.00 | Moderate complexity |
| 3 | MiniMax M2.7 | minimax/minimax-m2.7 | $0.30/$1.20 | **Background-only** (evolution daemon) |
| 4 | Claude Opus 4.6 | claude-opus-4-6 | $15.00/$75.00 | Premium — budget-gated |
| 5 | Ollama (local) | llama3.1 | $0/$0 | Offline fallback |

**M2.7 is never user-facing.** It only runs as the evolution daemon's analysis brain.

## Complexity Scoring

Rule-based scorer (<5ms, no LLM calls) with 5 features:

| Feature | Weight | Detection |
|---------|--------|-----------|
| Token count | 0.20 | Word count * 1.3 vs threshold |
| Requires tools | 0.15 | Tool-related keywords |
| Requires code | 0.15 | Code/dev-related keywords |
| Multi-step task | 0.20 | Sequential markers (then, after, finally) |
| Safety-critical | 0.30 | Security, financial, legal, production domains |

Plus domain-specific adjustments (security +0.15, creative -0.05, etc.)

### Score → Tier Mapping

| Score | Tier | Provider |
|-------|------|----------|
| < 0.4 | 1 | Nemotron 3 Super |
| 0.4 - 0.7 | 2 | MiMo-V2-Pro |
| > 0.7 | 4 | Claude Opus 4.6 (budget-gated) |

When Opus budget is exhausted, Tier 4 tasks cap at Tier 2.

## Configuration

### Provider Registry: `config/routing_config.yaml`

Defines all providers, their tiers, costs, and capabilities. The gateway reads this on startup.

### Scorer Weights: `config/scorer_weights.yaml`

Tunable weights for the complexity scorer. The evolution daemon modifies this file and the scorer hot-reloads.

### Split Tests: `config/split_tests.yaml`

A/B test definitions for routing changes. Created/managed by the split test manager.

## Budget Gating

```yaml
budget:
  opus_daily_usd: 15.00
  opus_monthly_usd: 100.00
  evolution_daily_usd: 5.00
  evolution_monthly_usd: 50.00
  total_monthly_usd: 200.00
```

When Opus budget is exhausted, the scorer caps Tier 4 requests at Tier 2 and marks `budget_gated: true` in the interaction log.

## Interaction Logging

Every routed request is logged to `data/interaction_log.db`:

- Routing decision (score, tier, provider, domain, features)
- Execution result (actual provider, latency, tokens, cost)
- Quality signals (success, error type, escalation, user correction)

### Query Helpers (`log_queries.py`)

| Query | Purpose |
|-------|---------|
| `get_failures_by_tier()` | Failure count/rate per tier |
| `get_escalation_rate()` | Override/escalation frequency |
| `get_cost_by_tier()` | Cost breakdown |
| `get_wins_by_tier()` | Clean win rate (no fallback, no escalation) |
| `get_domain_accuracy()` | Per-domain success/escalation |
| `get_scoring_drift()` | Score distribution per scorer version |
| `get_fallback_frequency()` | Provider reliability |
| `get_evolution_summary()` | All metrics in one call |

## Evolution Daemon

Background async daemon using M2.7 to continuously improve routing.

### 5-Step Cycle

1. **Collect**: Gather metrics from interaction log (24h window)
2. **Analyze**: Send to M2.7 (or rule-based fallback) for pattern detection
3. **Improve**: Generate bounded weight changes (max 20% per value per cycle)
4. **Validate**: Sanity checks — bounds, rate limits, tier gap preservation
5. **Deploy**: Write new `scorer_weights.yaml`, create versioned backup, hot-reload

### Safety Constraints

- Max 20% change per weight per cycle
- Weights stay in [0.0, 1.0]
- Tier thresholds maintain minimum 0.15 gap
- Minimum 20 interactions required to trigger cycle
- All changes auditable via versioned backups
- Rollback support: `deployer.rollback(to_version=N)`

### Running the Daemon

```bash
# Single cycle
python -m atlas.core.evolution.daemon --once

# Continuous (6-hour interval)
python -m atlas.core.evolution.daemon --interval 6

# Dry run (analyze but don't deploy)
python -m atlas.core.evolution.daemon --once --dry-run
```

## Metrics Dashboard

JSON endpoints for routing observability:

| Endpoint | Returns |
|----------|---------|
| `get_health()` | System status, failure/override rates |
| `get_routing()` | Tier wins, domain accuracy, fallbacks, drift |
| `get_cost()` | Cost breakdown by tier |
| `get_evolution()` | Daemon status and cycle history |
| `get_split_tests()` | Active A/B test results |
| `get_full_dashboard()` | All of the above |

## Split Testing

A/B testing framework for routing changes:

```python
from atlas.core.routing import SplitTestManager

mgr = SplitTestManager()
mgr.create_test(
    name="higher_safety_weight",
    experiment_overrides={"features.safety_critical_weight": 0.35},
)

# On each request:
assignment = mgr.assign(session_id)  # Deterministic by session
if assignment:
    # Apply overrides for experiment group
    ...

# Record outcome:
mgr.record_outcome(test_name, group, success=True, cost_usd=0.01)

# Check results:
results = mgr.get_results("higher_safety_weight")
# results["winner"] → "control" | "experiment" | "inconclusive"
```

## File Map

| File | Purpose |
|------|---------|
| `atlas/core/routing/__init__.py` | Package exports |
| `atlas/core/routing/provider_registry.py` | YAML-driven provider registry |
| `atlas/core/routing/complexity_scorer.py` | Rule-based complexity scorer |
| `atlas/core/routing/interaction_log.py` | SQLite interaction logger |
| `atlas/core/routing/log_queries.py` | Analytical queries for evolution |
| `atlas/core/routing/metrics.py` | JSON metrics dashboard |
| `atlas/core/routing/split_test.py` | A/B testing framework |
| `atlas/core/evolution/__init__.py` | Evolution daemon package |
| `atlas/core/evolution/daemon.py` | Main daemon orchestrator |
| `atlas/core/evolution/collector.py` | Metrics collection (Step 1) |
| `atlas/core/evolution/analyzer.py` | M2.7 / rule-based analysis (Step 2) |
| `atlas/core/evolution/improver.py` | Weight change generation (Step 3) |
| `atlas/core/evolution/validator.py` | Change validation (Step 4) |
| `atlas/core/evolution/deployer.py` | Hot deployment + rollback (Step 5) |
| `config/routing_config.yaml` | Provider registry config |
| `config/scorer_weights.yaml` | Scorer weights (evolution-tunable) |
| `atlas/tests/test_routing.py` | 56 tests across 5 phases |

## Environment Variables

| Variable | Required By |
|----------|-------------|
| `NVIDIA_API_KEY` | Nemotron 3 Super (Tier 1) |
| `OPENROUTER_API_KEY` | Qwen3.5, MiMo, M2.7 (Tiers 1-3) |
| `ANTHROPIC_API_KEY` | Claude Opus 4.6 (Tier 4) |
