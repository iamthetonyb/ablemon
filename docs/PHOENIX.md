# ATLAS Phoenix Observability

> Tracing, evaluation, and quality scoring for the ATLAS routing pipeline.

## Overview

Phoenix provides end-to-end observability for every request that flows through ATLAS. It traces provider calls, evaluates response quality, and feeds scores back into the distillation corpus builder.

## Setup

### Self-Hosted (Docker)

Phoenix runs as a self-hosted instance on `localhost:6006`:

```bash
docker run -d \
    --name atlas-phoenix \
    -p 6006:6006 \
    -v atlas-phoenix-data:/data \
    arizephoenix/phoenix:latest
```

Access the dashboard at `http://localhost:6006`.

### Environment

```bash
export PHOENIX_COLLECTOR_ENDPOINT="http://localhost:6006"
export PHOENIX_PROJECT_NAME="atlas-default"
```

### Fallback: JSONL Mode

When Phoenix is unavailable (Docker not running, network issues), traces fall back to local JSONL files:

```
~/.atlas/traces/
    YYYY-MM-DD.jsonl       # Daily trace files
    _fallback_buffer.jsonl  # Buffer for retry when Phoenix comes back
```

The fallback buffer is automatically replayed when Phoenix becomes available again.

## Span Model

Every traced operation produces a span with the following structure:

```
Trace (trace_id)
  |
  +-- Root Span: "atlas.request"
  |     attributes:
  |       atlas.session_id
  |       atlas.channel (cli, telegram, discord, api)
  |       atlas.tenant_id (multi-tenant mode)
  |
  +-- Child Span: "atlas.enricher"
  |     attributes:
  |       enricher.level (none, light, standard, deep)
  |       enricher.domains_detected
  |       enricher.flavor_words_expanded
  |
  +-- Child Span: "atlas.scorer"
  |     attributes:
  |       scorer.complexity_score
  |       scorer.selected_tier
  |       scorer.domain
  |       scorer.features (JSON)
  |       scorer.version
  |       scorer.budget_gated
  |
  +-- Child Span: "atlas.provider"
  |     attributes:
  |       provider.name
  |       provider.model_id
  |       provider.tier
  |       provider.fallback_used
  |       llm.input_tokens
  |       llm.output_tokens
  |       llm.latency_ms
  |       llm.cost_usd
  |
  +-- Child Span: "atlas.evaluation" (async, post-response)
        attributes:
          eval.hallucination_score
          eval.correctness_score
          eval.skill_adherence_score
          eval.tone_score
          eval.corpus_eligible
```

### Key Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `trace_id` | string | Unique identifier for the full request lifecycle |
| `span_id` | string | Unique identifier for each operation within a trace |
| `parent_span_id` | string | Links child spans to parent |
| `atlas.tenant_id` | string | Tenant identifier (empty for default tenant) |
| `eval.corpus_eligible` | bool | Whether this interaction qualifies for distillation |

## Instrumentors

Instrumentors automatically wrap provider calls to capture trace data without modifying business logic.

### Provider Instrumentors

Each provider type has a dedicated instrumentor:

| Provider | Instrumentor | What It Captures |
|----------|-------------|------------------|
| OpenAI (OAuth) | `OpenAIInstrumentor` | Request/response, tokens, latency, reasoning effort |
| Anthropic | `AnthropicInstrumentor` | Request/response, tokens, latency |
| OpenRouter | `OpenRouterInstrumentor` | Request/response, tokens, latency, model routing |
| NVIDIA NIM | `NIMInstrumentor` | Request/response, tokens, latency |
| Ollama | `OllamaInstrumentor` | Request/response, tokens, latency, local GPU metrics |

### Pipeline Instrumentors

| Component | What It Captures |
|-----------|------------------|
| TrustGate | Trust score, threat patterns detected |
| PromptEnricher | Enrichment level, domains, flavor words |
| ComplexityScorer | Score, tier, domain, feature weights |
| SplitTestManager | Test name, group assignment, overrides |

## Evaluators

Post-response evaluators score quality asynchronously. These scores feed into both the metrics dashboard and the distillation corpus builder.

### Hallucination Evaluator

Detects fabricated information in responses:
- Checks for unsupported factual claims
- Validates code references against actual codebase
- Flags inconsistencies with conversation context
- Score: 0.0 (no hallucination) to 1.0 (fully hallucinated)

### Correctness Evaluator

Assesses factual accuracy and task completion:
- Did the response address the user's request?
- Are code examples syntactically valid?
- Are referenced files/tools/APIs real?
- Score: 0.0 (incorrect) to 1.0 (fully correct)

### Skill Adherence Evaluator

Checks whether responses follow the active skill's SKILL.md instructions:
- Output format matches skill specification
- Required sections are present
- Tone and style match skill guidelines
- Score: 0.0 (non-adherent) to 1.0 (fully adherent)

### Tone Evaluator

Validates response tone against SOUL.md personality directives:
- No sycophantic patterns (forbidden phrases)
- Directness and conciseness
- Appropriate mirroring of user energy
- Score: 0.0 (off-brand) to 1.0 (perfectly on-brand)

## Corpus Eligibility Scoring

The `score_for_training` function combines evaluator scores to determine whether an interaction should be included in the distillation corpus:

```
corpus_eligible = (
    correctness >= 0.8
    AND hallucination <= 0.2
    AND skill_adherence >= 0.7
    AND tone >= 0.6
    AND success == True
    AND escalated == False
)
```

This flag is written to both the Phoenix span (`eval.corpus_eligible`) and the interaction log (`corpus_eligible` column), giving the corpus builder two sources to query.

## Per-Tenant Projects

In multi-tenant mode, each tenant gets an isolated Phoenix project:

```
atlas-default          # Default tenant
atlas-tenant-acme      # Tenant "acme"
atlas-tenant-globex    # Tenant "globex"
```

This provides:
- Isolated dashboards per tenant
- Tenant-specific evaluator scoring
- Per-tenant quality trends over time
- Data isolation for compliance

Set the project per request:

```python
import os
os.environ["PHOENIX_PROJECT_NAME"] = f"atlas-tenant-{tenant_id}"
```

## Integration Points

### With Interaction Log

Phoenix spans are correlated with interaction log records via `trace_id`. The interaction logger stores the trace_id, and Phoenix stores the full span tree.

### With Corpus Builder

The corpus builder queries both sources:
1. Interaction log: `WHERE corpus_eligible = 1` for candidate rows
2. Phoenix: Fetch evaluator scores for quality verification

### With Evolution Daemon

Phoenix evaluator scores feed into the evolution daemon's analysis:
- Low correctness scores on a tier indicate under-routing
- High hallucination rates on a provider trigger fallback preference
- Skill adherence trends inform enricher improvements

### With Metrics Dashboard

Phoenix data enriches the routing metrics dashboard:
- Quality scores per tier and provider
- Evaluator score distributions
- Corpus growth rate

## File Map

| File | Purpose |
|------|---------|
| `atlas/core/routing/interaction_log.py` | Interaction logger (stores trace_id) |
| `atlas/core/routing/metrics.py` | Metrics dashboard (reads Phoenix data) |
| `atlas/core/evolution/collector.py` | Evolution data collector (reads evaluator scores) |
| `config/routing_config.yaml` | Provider definitions (instrumentor mapping) |
