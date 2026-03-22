# Multi-Model Routing System

## Overview

ATLAS uses a 5-tier complexity-scored routing system that replaces the linear provider fallback chain with intelligent task-aware routing. An async self-evolution daemon continuously improves routing accuracy.

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
| 1 | GPT 5.4 Mini (xhigh) | gpt-5.4-mini (OpenAI OAuth) | $0 (sub) | Default — 70-80% of requests |
| 1 (fallback) | Nemotron 120B | nvidia/nemotron-3-super-120b-a12b (NIM) | $0.30/$0.80 | Fallback when Mini unavailable |
| 2 | GPT 5.4 (xhigh) | gpt-5.4 (OpenAI OAuth) | $0 (sub) | Complex reasoning |
| 2 (fallback) | MiMo-V2-Pro | xiaomi/mimo-v2-pro (OpenRouter) | $1.00/$3.00 | GPT 5.4 fallback |
| 3 | MiniMax M2.7 | minimax/minimax-m2.7 (OpenRouter) | $0.30/$1.20 | **Background-only** (evolution daemon) |
| 4 | Claude Opus 4.6 | claude-opus-4-6 (Anthropic) | $15.00/$75.00 | Premium — budget-gated |
| 5 | Qwen 3.5 27B/9B | qwen3.5-27b-ud / 9b-edge / 9b-balanced (Ollama) | $0/$0 | Offline + distillation base |

**M2.7 is never user-facing.** It only runs as the evolution daemon's analysis brain.

**GPT 5.4 Mini and GPT 5.4 route through OpenAI OAuth** (ChatGPT subscription), not OpenRouter or API keys. Authenticate once with `python scripts/atlas-auth.py` — tokens auto-refresh. OpenRouter is retained only for MiMo fallback and M2.7 evolution.

## Complexity Scoring

Rule-based scorer (<5ms, no LLM calls) with 5 features:

| Feature | Weight | Detection |
|---------|--------|-----------|
| Token count | 0.15 | Word count * 1.3 vs threshold |
| Requires tools | 0.15 | Tool-related keywords |
| Requires code | 0.20 | Code/dev-related keywords |
| Multi-step task | 0.20 | Sequential markers (then, after, finally) |
| Safety-critical | 0.30 | Security, financial, legal, production domains |

Plus domain-specific adjustments (security +0.15, creative -0.05, etc.)

### Score → Tier Mapping

| Score | Tier | Provider |
|-------|------|----------|
| < 0.4 | 1 | GPT 5.4 Mini (OpenAI OAuth) |
| 0.4 - 0.7 | 2 | GPT 5.4 (OpenAI OAuth) |
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

## Distillation Fields

The interaction logger can capture fields used by the distillation pipeline to build training corpora.

| Field | Type | Purpose |
|-------|------|---------|
| `corpus_eligible` | bool | Whether this interaction qualifies for training data |
| `raw_input` | text | Full user input (not truncated like `message_preview`) |
| `raw_output` | text | Full model output for training pairs |

Eligibility criteria:
- `success = true` and `user_correction = false`
- No fallback chain (clean single-provider completion)
- Output length > 50 tokens
- No PII detected in input/output

These fields feed into `data/distillation_*.jsonl` via the export pipeline. See `docs/DISTILLATION.md` for full pipeline docs.

## Tenant Routing

In multi-tenant mode, each tenant can override default routing behavior:

- **Tier thresholds**: Tenant A may use `tier_1_max: 0.35` (more T1) while Tenant B uses `0.45` (more T2)
- **Domain adjustments**: Per-tenant domain score overrides (e.g., legal-heavy tenant bumps legal +0.25)
- **Budget caps**: Per-tenant Opus budget limits
- **Tier restrictions**: Tenants can be restricted to specific tiers (e.g., no T4 access)

Tenant overrides are loaded from `config/tenants/{tenant_id}.yaml` and applied in the scorer before tier mapping. The interaction log tags every record with `tenant_id` for isolated analysis.

See `docs/MULTI-TENANT.md` for tenant lifecycle and config schema.

## Split Test Evolution

The evolution daemon and split testing framework integrate for safe weight deployment:

1. Daemon proposes weight changes based on interaction analysis
2. Instead of direct deployment, changes can be wrapped in a split test
3. Split test runs for N interactions (configurable, default 100)
4. If experiment group outperforms control (higher success rate, lower escalation), auto-promote
5. If inconclusive or worse, auto-rollback

```python
# Evolution daemon creates a split test for proposed changes
mgr.create_test(
    name=f"evo_v{new_version}",
    experiment_overrides=proposed_weight_changes,
    min_samples=100,
    auto_promote=True,
)
```

This adds a validation layer between the daemon's analysis and production deployment. Currently, the daemon deploys directly with rollback support. Split test integration is the next safety improvement.

## Thinking Token Dual-Path

Some models (Nemotron, Qwen 3.5 base) emit `<think>...</think>` tokens in their output. These need different handling for user-facing vs training contexts:

| Context | Handling |
|---------|----------|
| User-facing output | Strip `<think>` tokens completely |
| Interaction log | Store stripped output for quality signals |
| Distillation corpus | Preserve thinking tokens in `raw_output` as training signal |
| Phoenix spans | Log both stripped and raw versions |

The thinking process contains valuable reasoning traces. Stripping it from user output (where it's noise) while preserving it for training (where it's signal) maximizes the value of each interaction.

Implementation: Provider response handlers apply `strip_thinking_tokens()` before returning to user, but pass the raw response to the interaction logger when `corpus_eligible=true`.

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
| *(OpenAI OAuth)* | GPT 5.4 Mini (T1), GPT 5.4 (T2) — `python scripts/atlas-auth.py` |
| `OPENROUTER_API_KEY` | MiMo (Tier 2 fallback), M2.7 (Tier 3 evolution) |
| `NVIDIA_API_KEY` | Nemotron 120B (Tier 1 fallback, free NIM) |
| `ANTHROPIC_API_KEY` | Claude Opus 4.6 (Tier 4) |
