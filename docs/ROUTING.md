# Multi-Model Routing System

## Overview

ATLAS uses a 5-tier complexity-scored routing system that replaces the linear provider fallback chain with intelligent task-aware routing. An async self-evolution daemon continuously improves routing accuracy. A prompt enricher expands vague inputs into actionable prompts before scoring.

## Architecture

```
User Message
    |
    v
TrustGate (security scoring)
    |
    v
Scanner + Auditor (content analysis)
    |
    v
PromptEnricher (expand vague inputs)
    |
    v
SplitTestManager (A/B group assignment)
    |
    v
ComplexityScorer (rule-based, <5ms)
    |               |
    v               v
ProviderRegistry   InteractionLogger --> data/interaction_log.db
    |                                         |
    v                                    LogQueries
LLM Provider                                 |
    |                              EvolutionDaemon (M2.7, background)
    v                              |  Collect -> Analyze -> Improve -> Validate -> Deploy
Response                           |
                                   v
                              scorer_weights.yaml (hot-reloaded)
```

## Provider Tiers

| Tier | Provider | Model | Cost (in/out per M) | Context | Use Case |
|------|----------|-------|---------------------|---------|----------|
| 1 | GPT 5.4 Mini (xhigh) | gpt-5.4-mini (OpenAI OAuth) | $0 (sub) | 400K | Default -- 70-80% of requests |
| 1 (fallback) | Nemotron 120B | nvidia/nemotron-3-super-120b-a12b (NIM) | $0.30/$0.80 | 262K | Mini unavailable |
| 1 (last resort) | Nemotron 120B | nvidia/nemotron-3-super-120b-a12b:free (OpenRouter) | $0 | 262K | NIM down too |
| 2 | GPT 5.4 (xhigh) | gpt-5.4 (OpenAI OAuth) | $0 (sub) | 1M | Complex reasoning |
| 2 (fallback) | MiMo-V2-Pro | xiaomi/mimo-v2-pro (OpenRouter) | $1.00/$3.00 | 131K | GPT 5.4 fallback |
| 3 | MiniMax M2.7 | minimax/minimax-m2.7 (OpenRouter) | $0.30/$1.20 | 1M | **Background-only** (evolution daemon) |
| 4 | Claude Opus 4.6 | claude-opus-4-6 (Anthropic) | $15.00/$75.00 | 200K | Premium -- budget-gated |
| 5 | Qwen 3.5 27B | qwen3.5-27b-ud (Ollama) | $0 | 131K | Offline / distillation base |
| 5 (fallback) | Qwen 3.5 9B Edge | qwen3.5-9b-edge (Ollama) | $0 | 131K | Edge/mobile deployment |
| 5 (last resort) | Qwen 3.5 9B Balanced | qwen3.5-9b-balanced (Ollama) | $0 | 131K | Balanced edge option |
| 0 (future) | atlas-student-27b | Custom fine-tuned (Ollama) | $0 | 131K | Self-hosted student model |

**M2.7 is never user-facing.** It only runs as the evolution daemon's analysis brain.

**GPT 5.4 Mini and GPT 5.4 route through OpenAI OAuth** (ChatGPT subscription), not OpenRouter or API keys. Authenticate once with `python scripts/atlas-auth.py` -- tokens auto-refresh. OpenRouter is retained only for MiMo fallback and M2.7 evolution.

**Both T1 and T2 run at `xhigh` reasoning effort** -- maximum thinking depth on every request at $0 per token.

## Complexity Scoring

Rule-based scorer (<5ms, no LLM calls) with 5 weighted features:

| Feature | Weight | Detection |
|---------|--------|-----------|
| Token count | 0.15 | Word count * 1.3 vs 2000-token threshold |
| Requires tools | 0.15 | Tool-related keywords (deploy, search, etc.) |
| Requires code | 0.20 | Code/dev-related keywords |
| Multi-step task | 0.20 | Sequential markers (then, after, finally) |
| Safety-critical | 0.30 | Security, financial, legal, production domains |

Plus domain-specific adjustments:

| Domain | Adjustment | Rationale |
|--------|-----------|-----------|
| security | +0.20 | Under-routing risk (validated by eval data, v2) |
| financial | +0.15 | High stakes |
| legal | +0.15 | High stakes |
| coding | +0.10 | Complexity tends to be higher |
| production | +0.10 | Impact risk |
| research | +0.05 | Moderate |
| planning | +0.05 | Moderate |
| creative | -0.05 | T1 handles well |

### Score to Tier Mapping

| Score | Tier | Provider |
|-------|------|----------|
| < 0.4 | 1 | GPT 5.4 Mini (OpenAI OAuth, xhigh) |
| 0.4 - 0.7 | 2 | GPT 5.4 (OpenAI OAuth, xhigh) |
| > 0.7 | 4 | Claude Opus 4.6 (budget-gated) |

When Opus budget is exhausted, Tier 4 tasks cap at Tier 2.

## Configuration

### Provider Registry: `config/routing_config.yaml`

Defines all providers, their tiers, costs, and capabilities. The gateway reads this on startup.

### Scorer Weights: `config/scorer_weights.yaml`

Tunable weights for the complexity scorer. The evolution daemon modifies this file and the scorer hot-reloads. Current version: v2 (2026-03-19).

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

### Distillation Integration

The interaction log supports a `corpus_eligible` field for distillation pipeline integration. When a response meets quality criteria (success, no escalation, adequate complexity), it can be flagged as eligible for the training corpus.

Eligibility criteria:
- `success = 1` -- request completed without error
- `escalated = 0` -- tier was appropriate (no under-routing)
- `user_correction = 0` -- user did not override the routing
- Provider was Tier 2+ (higher-quality responses for distillation)

The distillation harvesters query this field when building training corpora. See `docs/DISTILLATION.md` for the full pipeline.

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
4. **Validate**: Sanity checks -- bounds, rate limits, tier gap preservation
5. **Deploy**: Write new `scorer_weights.yaml`, create versioned backup, hot-reload

### Safety Constraints

- Max 20% change per weight per cycle
- Weights stay in [0.0, 1.0]
- Tier thresholds maintain minimum 0.15 gap
- Minimum 20 interactions required to trigger cycle
- All changes auditable via versioned backups in `data/evolution_cycles/`
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

### Tuning History

- **v1** (2026-03-17): Initial weights from domain heuristics
- **v2** (2026-03-19): Security weight bumped +0.20 (from +0.15) -- 7 under-routes on security prompts detected in eval data

## Split Testing

A/B testing framework for routing changes. The evolution daemon can propose weight changes as split tests instead of deploying directly.

### How Split Tests Work

1. **Creation**: A test defines `experiment_overrides` -- a dict of weight paths to modified values (e.g., `{"features.safety_critical_weight": 0.35}`).
2. **Assignment**: Each request is deterministically assigned to control or experiment via `sha256(session_id + test_name)`. Same session always gets same group.
3. **Tracking**: Outcomes (success, escalation, cost, latency) are recorded per group.
4. **Conclusion**: After minimum 30 samples per group, a winner is computed based on success rate, escalation rate, and cost.

### Integration with Evolution

The evolution daemon can operate in two modes:
- **Direct deploy** (default): Changes go straight to `scorer_weights.yaml`
- **Split test mode**: Changes are wrapped in a split test, run for N interactions, then promoted or rolled back based on results

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
# results["winner"] -> "control" | "experiment" | "inconclusive"
```

### Winner Determination

| Condition | Winner |
|-----------|--------|
| experiment success_rate > control AND experiment escalation <= control | experiment |
| control success_rate > experiment | control |
| Equal success_rate AND experiment cheaper | experiment |
| < 30 samples per group | inconclusive |

## Tier 0: Self-Hosted Student Models

When distillation produces a validated student model, it can be promoted to Tier 0 -- a self-hosted tier that handles simple requests before they reach the API-based tiers. A student model must pass the 4-stage validation gate before promotion (see `docs/DISTILLATION.md`).

When enabled, Tier 0 sits below the current tier_1_max threshold:

| Score | Tier | Provider |
|-------|------|----------|
| < 0.2 | 0 | atlas-student-27b (Ollama, local) |
| 0.2 - 0.4 | 1 | GPT 5.4 Mini (OpenAI OAuth) |
| 0.4 - 0.7 | 2 | GPT 5.4 (OpenAI OAuth) |
| > 0.7 | 4 | Claude Opus 4.6 (budget-gated) |

Tier 0 is disabled by default. Enable via `config/routing_config.yaml`:

```yaml
routing:
  tier_0_enabled: false
  tier_0_max_score: 0.2
  tier_0_model: "atlas-student-27b"
```

## Tenant Routing

In multi-tenant mode, each tenant can have independent tier restrictions, budget caps, and scorer weight overrides. The complexity scorer checks tenant config before routing, capping at the tenant's `max_tier` regardless of score.

See `docs/MULTI-TENANT.md` for tenant configuration, data isolation, billing, and training scheduler details.

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
| `atlas/core/routing/prompt_enricher.py` | Prompt enrichment (rule-based) |
| `atlas/core/evolution/__init__.py` | Evolution daemon package |
| `atlas/core/evolution/daemon.py` | Main daemon orchestrator |
| `atlas/core/evolution/collector.py` | Metrics collection (Step 1) |
| `atlas/core/evolution/analyzer.py` | M2.7 / rule-based analysis (Step 2) |
| `atlas/core/evolution/improver.py` | Weight change generation (Step 3) |
| `atlas/core/evolution/validator.py` | Change validation (Step 4) |
| `atlas/core/evolution/deployer.py` | Hot deployment + rollback (Step 5) |
| `atlas/core/evolution/auto_improve.py` | Failure classification + auto-fix |
| `config/routing_config.yaml` | Provider registry config |
| `config/scorer_weights.yaml` | Scorer weights (evolution-tunable) |
| `config/split_tests.yaml` | A/B test definitions |
| `atlas/tests/test_routing.py` | 56 tests across 5 phases |

## Environment Variables

| Variable | Required By |
|----------|-------------|
| *(OpenAI OAuth)* | GPT 5.4 Mini (T1), GPT 5.4 (T2) -- `python scripts/atlas-auth.py` |
| `OPENROUTER_API_KEY` | MiMo (Tier 2 fallback), M2.7 (Tier 3 evolution) |
| `NVIDIA_API_KEY` | Nemotron 120B (Tier 1 fallback, free NIM) |
| `ANTHROPIC_API_KEY` | Claude Opus 4.6 (Tier 4) |
