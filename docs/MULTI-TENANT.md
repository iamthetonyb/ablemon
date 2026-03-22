# Multi-Tenant System

## Overview

ATLAS supports multiple tenants (clients/instances) with isolated data, routing, billing, and model training. Each tenant operates as if they have their own ATLAS instance while sharing infrastructure.

## Tenant Lifecycle

```
Onboard → Operate → Improve → Distill → Serve
```

1. **Onboard**: Create tenant config, provision isolated storage, set routing overrides
2. **Operate**: Tenant requests route through shared infrastructure with per-tenant scoring
3. **Improve**: Evolution daemon runs per-tenant analysis cycles
4. **Distill**: Tenant corpus trains a dedicated fine-tuned model on H100
5. **Serve**: Tenant gets their own Ollama model registered in T5

## Data Isolation (NON-NEGOTIABLE)

Tenant A never sees tenant B's data. This is enforced at every layer:

| Layer | Isolation Method |
|-------|-----------------|
| Interaction log | `tenant_id` column, all queries filter by tenant |
| Training corpus | Separate JSONL files: `data/distillation_{tenant_id}_*.jsonl` |
| Memory | Separate SQLite databases per tenant |
| Phoenix | Separate projects per tenant |
| Billing | Per-tenant usage tracking and invoicing |
| Secrets | Tenant-scoped secrets in `~/.atlas/.secrets/{tenant_id}/` |
| Ollama models | Tenant-prefixed model names: `{tenant_id}-qwen-27b-v1` |

### Query Isolation

Every database query that touches tenant data MUST include a tenant filter:

```python
# CORRECT
rows = db.execute("SELECT * FROM interaction_log WHERE tenant_id = ?", (tenant_id,))

# WRONG — leaks cross-tenant data
rows = db.execute("SELECT * FROM interaction_log")
```

## Per-Tenant Routing

Each tenant can override default routing behavior:

```yaml
# config/tenants/{tenant_id}.yaml
tenant_id: "acme-corp"
name: "Acme Corporation"
enabled: true

routing:
  # Override tier thresholds
  tier_1_max_score: 0.35    # More aggressive T1 usage (cost savings)
  tier_2_max_score: 0.65

  # Override domain adjustments
  domain_overrides:
    legal: 0.25             # Tenant does heavy legal work
    creative: -0.10         # Deprioritize creative routing

  # Budget caps (per tenant)
  opus_daily_usd: 5.00
  opus_monthly_usd: 30.00

  # Allowed tiers (restrict expensive tiers)
  allowed_tiers: [1, 2, 5]  # No T4 access

billing:
  rate_per_hour: 150.00
  currency: "USD"
  invoice_frequency: "monthly"

distillation:
  enabled: true
  corpus_min_pairs: 500
  auto_train: false         # Require manual approval before H100 run
```

## Per-Tenant Billing

Usage tracked per tenant via interaction log:

```python
from atlas.billing import TenantBilling

billing = TenantBilling(tenant_id="acme-corp")
summary = billing.get_period_summary(start="2026-03-01", end="2026-03-31")
# {
#   "total_requests": 1247,
#   "total_cost_usd": 23.45,
#   "by_tier": {"T1": 980, "T2": 250, "T4": 17},
#   "training_hours_used": 2.5
# }
```

## Per-Tenant Dashboards

Each tenant gets isolated Phoenix observability:

- Phoenix project: `tenant_{tenant_id}`
- Dashboard: `http://localhost:6006/projects/tenant_{tenant_id}`
- Spans tagged with `atlas.tenant_id` for filtering

## GPU Training Schedule

H100 time is shared across tenants. Monthly allocation:

| Allocation | Hours | Schedule |
|------------|-------|----------|
| Core ATLAS model | 8h | Weekly (Sundays) |
| Tenant training | 10h | Queued, first-come-first-served |
| Buffer | 2h | Re-runs, experiments |

Training queue:

```bash
# Submit tenant training job
python -m atlas.core.tenants.train --tenant acme-corp --corpus data/distillation_acme-corp_all.jsonl

# Check queue
python -m atlas.core.tenants.train --queue

# Priority override (operator only)
python -m atlas.core.tenants.train --tenant acme-corp --priority high
```

## Tenant Config Schema

```yaml
tenant_id: string          # Unique identifier (kebab-case)
name: string               # Display name
enabled: bool              # Active/inactive toggle
created_at: datetime       # ISO 8601

routing:
  tier_1_max_score: float  # Default: 0.4
  tier_2_max_score: float  # Default: 0.7
  domain_overrides: dict   # Per-domain score adjustments
  opus_daily_usd: float    # Per-tenant Opus budget
  opus_monthly_usd: float
  allowed_tiers: list[int] # Which tiers this tenant can access

billing:
  rate_per_hour: float
  currency: string
  invoice_frequency: string  # weekly | monthly | per-project

distillation:
  enabled: bool
  corpus_min_pairs: int    # Minimum pairs before training
  auto_train: bool         # Auto-submit to H100 queue when ready
  model_prefix: string     # Ollama model name prefix

context:
  industry: string         # For domain-specific enrichment
  tone: string             # For copywriting skill
  custom_skills: list      # Additional skill triggers
```

## CLI Commands

```bash
# Onboard new tenant
python -m atlas.core.tenants.onboard --id acme-corp --name "Acme Corporation"

# List tenants
python -m atlas.core.tenants.list

# Tenant status (routing stats, corpus size, billing)
python -m atlas.core.tenants.status --tenant acme-corp

# Update tenant config
python -m atlas.core.tenants.config --tenant acme-corp --set routing.opus_daily_usd=10.00

# Disable tenant
python -m atlas.core.tenants.config --tenant acme-corp --set enabled=false

# Export tenant corpus for training
python -m atlas.core.distillation.export --tenant acme-corp --output data/distillation_acme-corp_all.jsonl

# Generate tenant invoice
python -m atlas.billing.invoice --tenant acme-corp --period 2026-03
```

## File Map

| File | Purpose |
|------|---------|
| `config/tenants/{tenant_id}.yaml` | Per-tenant configuration |
| `atlas/core/tenants/manager.py` | Tenant CRUD and config loading |
| `atlas/core/tenants/router.py` | Per-tenant routing overrides |
| `atlas/core/tenants/isolation.py` | Data isolation enforcement |
| `atlas/billing/tenant_billing.py` | Per-tenant usage tracking |
| `data/distillation_{tenant_id}_*.jsonl` | Tenant training corpus |
