# ATLAS Multi-Tenant System

> Per-tenant routing, billing, training, and observability.

## Overview

Multi-tenancy allows ATLAS to serve multiple clients/organizations with isolated data, independent budgets, custom routing, and per-tenant distilled models.

## Tenant Lifecycle

```
Onboard --> Operate --> Improve --> Distill --> Serve
   |           |           |          |          |
   |           |           |          |          +-- Custom student model in production
   |           |           |          +-- Train tenant-specific model on H100
   |           |           +-- Evolution daemon tunes tenant weights
   |           +-- Requests routed through tenant config
   +-- Tenant config created, budget set, routing configured
```

### 1. Onboard

Create a tenant configuration and provision resources:

```bash
python -m atlas.core.tenants.manage --create \
    --id acme \
    --name "Acme Corp" \
    --contact "admin@acme.com" \
    --budget-daily 10.00 \
    --budget-monthly 100.00 \
    --max-tier 4
```

This creates:
- Tenant config at `~/.atlas/tenants/acme/config.yaml`
- Isolated Phoenix project `atlas-tenant-acme`
- Budget tracking entry in billing system
- Interaction log index on `(tenant_id, timestamp)`

### 2. Operate

Requests include `tenant_id` and are routed through the tenant's configuration:
- Complexity scoring uses tenant-specific weight overrides (if any)
- Budget gating checks the tenant's caps, not the global caps
- Interaction log tags all records with `tenant_id`
- Phoenix traces go to the tenant's project

### 3. Improve

The evolution daemon can optimize weights per tenant:
- Requires minimum 50 interactions from the tenant
- Produces tenant-specific `scorer_weights.yaml` in the tenant's config directory
- Changes are bounded by the same safety constraints as global evolution

### 4. Distill

When a tenant accumulates enough high-quality interactions:
- Corpus builder creates a tenant-isolated training set
- Training scheduler allocates GPU hours from the tenant's budget
- Student model is trained on tenant data only (no cross-tenant leakage)

### 5. Serve

Validated tenant-specific student model is deployed:
- Registered in Ollama as `atlas-student-{tenant_id}`
- Available as the tenant's Tier 0 provider
- Monitored via the tenant's Phoenix project

## Data Isolation

| Data Type | Isolation Method |
|-----------|-----------------|
| Interaction log | `tenant_id` column, indexed queries |
| Phoenix traces | Separate Phoenix project per tenant |
| Training corpus | Per-tenant corpus directory |
| Student models | Per-tenant Ollama model name |
| Budget tracking | Per-tenant billing records |
| Scorer weights | Per-tenant `scorer_weights.yaml` |

Cross-tenant data access is prevented at the query layer. All tenant-scoped queries filter on `tenant_id`.

## Per-Tenant Routing

### Tier Overrides

Each tenant can restrict which tiers are available:

```yaml
routing_overrides:
  max_tier: 2          # Cap at Tier 2 (no Opus)
  min_tier: 1          # Floor at Tier 1
  tier_0_enabled: false # No custom student model yet
```

When `max_tier` is set, the scorer caps routing at that tier regardless of complexity score. This prevents unexpectedly expensive requests on a tenant's budget.

### Budget Gating

Per-tenant budget caps override the global caps:

```yaml
budget:
  daily_usd: 10.00
  monthly_usd: 100.00
  opus_daily_usd: 5.00    # Subset of daily for Opus specifically
  opus_monthly_usd: 30.00
```

When a tenant's budget is exhausted:
1. Tier 4 requests cap at `max_tier` or Tier 2 (whichever is lower)
2. `budget_gated: true` is set in the interaction log
3. Tenant is notified via their configured channel

### Weight Overrides

Tenants can have custom scorer weights that differ from the global defaults:

```yaml
scorer_overrides:
  features:
    safety_critical_weight: 0.40  # Tenant deals with financial data
  domain_adjustments:
    financial: 0.25               # Higher than global 0.15
```

These are merged on top of the global `scorer_weights.yaml` at scoring time.

## Billing

### Markup

Tenant billing supports configurable markup on provider costs:

```yaml
billing:
  markup_pct: 20          # 20% markup on raw provider costs
  minimum_monthly: 50.00  # Minimum monthly charge
  gpu_hourly_rate: 3.50   # Per-hour charge for H100 training time
```

### GPU Allocation

Training time is allocated from the tenant's budget:

```yaml
training:
  gpu_hours_monthly: 4.0        # Allocated H100 hours per month
  gpu_hours_used: 1.2           # Used this period
  priority: "normal"            # normal | high | low
```

### ROI Tracking

The billing system tracks cost reduction from tenant-specific student models:

```
ROI = (cost_before_student - cost_after_student) / training_cost
```

Metrics:
- Pre-student monthly cost (API calls to T1/T2/T4)
- Post-student monthly cost (local T0 handles N% of requests)
- Cumulative training investment
- Break-even projection

## Training Scheduler

The training scheduler manages GPU time across tenants using a priority queue.

### Priority Queue

| Priority | Criteria |
|----------|---------|
| high | Tenant with largest corpus and highest ROI potential |
| normal | Standard tenants with adequate corpus |
| low | New tenants still accumulating data |

### Budget Allocation

Monthly GPU hours are split:
- 8h core ATLAS model (shared across all tenants)
- 12h tenant-specific training (allocated by priority)
- 2.5h buffer for retries and validation

### Scheduling Algorithm

```
1. Sort tenants by priority (high -> normal -> low)
2. For each tenant:
   a. Check corpus readiness (min 500 examples)
   b. Check GPU budget remaining
   c. Estimate training time (corpus_size * hours_per_1k)
   d. If fits in budget, schedule
   e. If not, defer to next period
3. Run scheduled jobs sequentially on H100
```

## Dashboard

Per-tenant metrics available via the routing metrics dashboard:

| Metric | Description |
|--------|-------------|
| Request volume | Total requests, by tier, by domain |
| Quality scores | Avg correctness, hallucination, skill adherence |
| Cost breakdown | By tier, by provider, total |
| Budget utilization | Daily/monthly spend vs caps |
| Model status | Student model version, validation state |
| ROI | Cost savings from student model |

## CLI Commands

```bash
# Tenant management
python -m atlas.core.tenants.manage --create --id acme --name "Acme Corp"
python -m atlas.core.tenants.manage --list
python -m atlas.core.tenants.manage --show acme
python -m atlas.core.tenants.manage --update acme --budget-monthly 200.00
python -m atlas.core.tenants.manage --disable acme

# Tenant metrics
python -m atlas.core.tenants.manage --metrics acme
python -m atlas.core.tenants.manage --metrics acme --period 30d

# Tenant training
python -m atlas.core.tenants.manage --training-status acme
python -m atlas.core.tenants.manage --schedule-training acme

# Tenant billing
python -m atlas.core.tenants.manage --billing acme
python -m atlas.core.tenants.manage --billing acme --period 2026-03
python -m atlas.core.tenants.manage --invoice acme --period 2026-03
```

## Example Tenant Config

Full tenant configuration at `~/.atlas/tenants/acme/config.yaml`:

```yaml
tenant:
  id: "acme"
  name: "Acme Corp"
  contact: "admin@acme.com"
  created_at: "2026-03-21T00:00:00Z"
  status: "active"  # active | suspended | disabled

routing_overrides:
  max_tier: 4
  min_tier: 1
  tier_0_enabled: false
  tier_0_model: null

scorer_overrides:
  features:
    safety_critical_weight: 0.35
  domain_adjustments:
    financial: 0.20

budget:
  daily_usd: 10.00
  monthly_usd: 100.00
  opus_daily_usd: 5.00
  opus_monthly_usd: 30.00

billing:
  markup_pct: 20
  minimum_monthly: 50.00
  gpu_hourly_rate: 3.50

training:
  gpu_hours_monthly: 4.0
  gpu_hours_used: 0.0
  priority: "normal"
  corpus_path: "~/.atlas/tenants/acme/corpus/"
  model_name: "atlas-student-acme"

notifications:
  budget_warning_pct: 80    # Alert at 80% budget usage
  channel: "telegram"        # Where to send alerts
```

## File Map

| File | Purpose |
|------|---------|
| `~/.atlas/tenants/{id}/config.yaml` | Per-tenant configuration |
| `~/.atlas/tenants/{id}/scorer_weights.yaml` | Per-tenant scorer weights |
| `~/.atlas/tenants/{id}/corpus/` | Per-tenant training corpus |
| `atlas/core/routing/interaction_log.py` | Tenant-tagged interaction records |
| `atlas/core/routing/complexity_scorer.py` | Tenant-aware scoring |
| `atlas/core/evolution/daemon.py` | Per-tenant evolution cycles |
| `config/routing_config.yaml` | Global provider definitions |
