# Improvements Discovered During Build

> Log of improvements, optimizations, and architectural insights discovered during ATLAS infrastructure development.
> Tag each entry: `IMPLEMENTED` | `SUGGESTED` | `BLOCKED`

---

## Phase 1: Routing Infrastructure

### Complexity Scorer

- `IMPLEMENTED` — Domain-specific weight adjustments (security +0.20, coding +0.10) after eval data showed 7 under-routes on security/code prompts. Scorer weights v2 deployed.
- `IMPLEMENTED` — Hot-reload for scorer weights. Evolution daemon writes new `scorer_weights.yaml` and scorer picks up changes without restart.
- `SUGGESTED` — Add conversation-history-aware scoring. Multi-turn conversations that start simple but escalate should trigger mid-conversation tier promotion.
- `SUGGESTED` — Token count feature should account for expected output length, not just input length. Long-output tasks (full code files, reports) need higher tiers even with short prompts.

### Provider Registry

- `IMPLEMENTED` — YAML-driven provider config. Adding/removing providers requires no code changes.
- `IMPLEMENTED` — Fallback chains defined per-provider in config. Automatic failover without orchestrator changes.
- `SUGGESTED` — Add provider health tracking. If a provider has >3 consecutive failures, temporarily deprioritize it (circuit breaker pattern).

### Interaction Logging

- `IMPLEMENTED` — WAL mode SQLite for concurrent read/write (evolution daemon reads while gateway writes).
- `SUGGESTED` — Add `raw_input` and `raw_output` fields to interaction log for distillation corpus harvesting. Currently only stores `message_preview` (200 chars).
- `SUGGESTED` — Add `corpus_eligible` boolean flag to interaction log. Harvesters can filter on this instead of re-evaluating eligibility on every export.

---

## Phase 2: Evolution Daemon

### Self-Evolving Weights

- `IMPLEMENTED` — 5-step cycle (Collect, Analyze, Improve, Validate, Deploy) with safety constraints (max 20% change per weight per cycle).
- `IMPLEMENTED` — Versioned backups of scorer_weights.yaml with rollback support.
- `IMPLEMENTED` — Eval-driven auto-improvement: failure classification feeds back into scorer adjustments.
- `SUGGESTED` — Track confidence intervals on weight changes. If the daemon is uncertain (< 20 interactions in a domain), it should be more conservative.
- `BLOCKED` — M2.7 analysis sometimes generates recommendations outside the bounded change range. The validator catches these, but the analyzer should be prompted to respect bounds upfront.

### Split Testing

- `IMPLEMENTED` — A/B test framework with deterministic session assignment and statistical significance tracking.
- `SUGGESTED` — Auto-promote winning split test variants after significance threshold is met (currently requires manual promotion).
- `SUGGESTED` — Evolution daemon should auto-create split tests for its proposed weight changes instead of deploying directly. This adds a validation layer.

---

## Phase 3: Prompt Enricher

- `IMPLEMENTED` — Rule-based enricher (0ms, $0) that expands flavor words into domain-specific criteria.
- `IMPLEMENTED` — 4 enrichment levels (none, light, standard, deep) based on intent detection.
- `SUGGESTED` — Add enrichment templates per skill. When a skill triggers, the enricher should use skill-specific expansion rules.
- `SUGGESTED` — Track enrichment effectiveness per domain. If enrichment consistently doesn't improve T1 output quality for a domain, skip it (saves context window).

---

## Phase 4: Distillation Pipeline

- `IMPLEMENTED` — Switched T5 from standard Qwen quants to Unsloth Dynamic 2.0 quants (measurably better accuracy at same bit width).
- `IMPLEMENTED` — Three Ollama Modelfile configs for server (27B), edge (9B IQ2_M), and balanced (9B Q4_K_XL).
- `SUGGESTED` — Implement thinking token dual-path: strip `<think>` tokens for user-facing output but preserve them in distillation corpus. The thinking process is valuable training signal.
- `SUGGESTED` — Domain-weighted corpus sampling. Security and code domains should be overrepresented in training data relative to their frequency in production traffic.
- `BLOCKED` — Need 500+ training pairs before first H100 run. Currently at ~20 pairs. Bottleneck is eval run frequency.

---

## Phase 5: Observability (Phoenix)

- `SUGGESTED` — Add OpenTelemetry instrumentation to all provider calls. Each span should carry routing metadata (tier, score, domain, provider).
- `SUGGESTED` — Run Phoenix evaluators (Hallucination, QACorrectness) on sampled production traffic to catch quality regressions before they compound.
- `SUGGESTED` — Teacher-student experiments in Phoenix for comparing T4 vs T5 outputs. Failed comparisons become high-priority distillation targets.

---

## Phase 6: Multi-Tenant

- `SUGGESTED` — Tenant data isolation via `tenant_id` column on all data tables + mandatory query filtering.
- `SUGGESTED` — Per-tenant routing overrides (tier thresholds, domain adjustments, budget caps) via tenant config files.
- `SUGGESTED` — GPU training queue system for fair H100 allocation across tenants.

---

## Cross-Cutting

- `IMPLEMENTED` — OAuth PKCE for GPT 5.4 Mini/GPT 5.4 — $0 per token via ChatGPT subscription.
- `IMPLEMENTED` — Thinking bleed fix: T1 model swapped from Nemotron (20%) to GPT 5.4 Mini (100%) after shootout revealed `<think>` token leakage.
- `SUGGESTED` — Unified cost tracking across all tiers. Currently billing and interaction log track costs separately.
- `SUGGESTED` — Add a `/cost` CLI command that shows real-time spend by tier, provider, and tenant with daily/monthly projections.
