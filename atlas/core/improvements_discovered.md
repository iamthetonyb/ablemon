# Improvements Discovered During Build

> Log of improvements found while building ATLAS infrastructure.
> Tagged: [IMPLEMENTED] | [SUGGESTED] | [BLOCKED]

## Phase 1-3: Routing + Scoring + Logging

- [IMPLEMENTED] Interaction log uses ALTER TABLE for backward-compatible migration
- [SUGGESTED] Add TTL-based cleanup for old interaction records (>90 days)
- [SUGGESTED] Add index on (tenant_id, timestamp) for faster tenant queries

## Phase 4: Evolution Daemon

- [SUGGESTED] Add circuit breaker for M2.7 API calls in evolution daemon
- [SUGGESTED] Cache morning report for 1 hour to avoid redundant queries

## Phase 5: Metrics + Split Testing

- [SUGGESTED] Add WebSocket support for real-time metrics streaming

## Phase 6: Phoenix Observability

- [SUGGESTED] Add sampling rate config (trace 100% in dev, 10% in prod)
- [BLOCKED] Phoenix Docker setup needs compose file (waiting for containerization)

## Phase 7-8: Harvesters + Corpus

- [IMPLEMENTED] Content hash deduplication prevents cross-platform duplicates
- [SUGGESTED] Add incremental corpus builds (append-only, not full rebuild)

## Phase 9-10: Training + Validation

- [SUGGESTED] Add Weights & Biases integration for training monitoring
- [SUGGESTED] Add automatic learning rate scheduling based on corpus size

## Phase 11: Multi-Tenant

- [SUGGESTED] Add tenant onboarding wizard (interactive CLI)
- [SUGGESTED] Add tenant health check cron (detect inactive tenants)

## Cross-Phase Synergies

- [SUGGESTED] Phoenix evaluator scores could feed directly into complexity scorer calibration
- [SUGGESTED] Tenant training scheduler could batch similar-domain tenants for efficiency
- [SUGGESTED] Morning report could include per-tenant mini-reports
