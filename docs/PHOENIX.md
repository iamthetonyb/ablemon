# Arize Phoenix Observability

## Overview

Phoenix provides LLM observability for ATLAS via OpenTelemetry tracing. Every provider call is instrumented as a span with routing metadata, token counts, latency, and quality signals.

Self-hosted at `http://localhost:6006`. No data leaves the local machine.

## Setup

```bash
pip install arize-phoenix opentelemetry-api opentelemetry-sdk

# Start Phoenix server
python -m phoenix.server.main serve --port 6006

# Or run as background daemon
nohup python -m phoenix.server.main serve --port 6006 &
```

Phoenix UI: `http://localhost:6006`

## Instrumentation

All provider calls (OpenAI OAuth, Anthropic, OpenRouter, Ollama) are wrapped with OpenTelemetry spans.

### Span Catalog

Every span includes this metadata:

| Attribute | Type | Description |
|-----------|------|-------------|
| `atlas.tier` | int | Selected tier (1/2/3/4/5) |
| `atlas.provider` | string | Provider name from routing_config.yaml |
| `atlas.model` | string | Model ID (e.g., `gpt-5.4-mini`) |
| `atlas.complexity_score` | float | Complexity scorer output (0.0-1.0) |
| `atlas.domain` | string | Detected domain (coding, security, etc.) |
| `atlas.scorer_version` | int | Scorer weights version |
| `atlas.budget_gated` | bool | Whether Opus budget cap was hit |
| `atlas.fallback_used` | bool | Whether fallback provider was triggered |
| `atlas.fallback_chain` | string | Comma-separated providers tried |
| `atlas.channel` | string | Source channel (cli, telegram, discord) |
| `atlas.session_id` | string | Session identifier |
| `atlas.tenant_id` | string | Tenant identifier (multi-tenant mode) |
| `atlas.enrichment_level` | string | Enricher level applied (none/light/standard/deep) |
| `llm.input_tokens` | int | Input token count |
| `llm.output_tokens` | int | Output token count |
| `llm.latency_ms` | float | Provider response time |
| `llm.cost_usd` | float | Estimated cost |
| `llm.success` | bool | Whether the call succeeded |
| `llm.error_type` | string | Error category if failed |

### Provider Instrumentation

```python
from opentelemetry import trace

tracer = trace.get_tracer("atlas.providers")

with tracer.start_as_current_span("llm_call") as span:
    span.set_attribute("atlas.tier", scoring_result.selected_tier)
    span.set_attribute("atlas.provider", provider_name)
    span.set_attribute("atlas.model", model_id)
    span.set_attribute("atlas.complexity_score", scoring_result.score)
    span.set_attribute("atlas.domain", scoring_result.domain)
    # ... make the actual API call
    span.set_attribute("llm.latency_ms", elapsed_ms)
    span.set_attribute("llm.output_tokens", response.usage.output_tokens)
```

## Evaluators

Phoenix evaluators run on completed spans to assess output quality.

| Evaluator | What It Measures | When |
|-----------|-----------------|------|
| Hallucination | Factual grounding of responses | All T1/T2 responses |
| QACorrectness | Answer accuracy vs expected output | Eval runs |
| SkillAdherence | Whether skill protocols were followed | Skill-triggered responses |
| Tone | Voice/style consistency with context rules | Copywriting domain |

### Running Evaluators

```python
from phoenix.evals import HallucinationEvaluator, QACorrectnessEvaluator

# Evaluate a batch of spans
hallucination_eval = HallucinationEvaluator(model=eval_model)
results = hallucination_eval.evaluate(spans_df)
```

## Teacher-Student Comparison

Phoenix experiments compare teacher (T4 Opus) and student (T5 Ollama) outputs side-by-side.

### Workflow

1. Run eval suite against both T4 and T5 on same prompts
2. Log both outputs as linked spans with `atlas.experiment_id`
3. Phoenix experiment view shows win/loss/tie per prompt
4. Results feed into distillation pipeline — failed prompts become high-priority training targets

### Creating an Experiment

```python
import phoenix as px

experiment = px.Experiment(
    name="t4-vs-t5-security-v2",
    description="Compare Opus vs fine-tuned Qwen on security domain",
)

# Log teacher span
experiment.log_run(
    input=prompt,
    output=opus_response,
    metadata={"provider": "claude-opus-4-6", "tier": 4},
)

# Log student span
experiment.log_run(
    input=prompt,
    output=ollama_response,
    metadata={"provider": "ollama-local", "tier": 5},
)
```

## Per-Tenant Projects

In multi-tenant mode, each tenant gets its own Phoenix project for data isolation.

```python
# Create tenant-specific project
px.Client().create_project(name=f"tenant_{tenant_id}")

# Tag spans with tenant
span.set_attribute("atlas.tenant_id", tenant_id)
span.set_attribute("phoenix.project", f"tenant_{tenant_id}")
```

Tenant dashboards are accessible at `http://localhost:6006/projects/tenant_{id}`.

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `PHOENIX_PORT` | 6006 | Phoenix server port |
| `PHOENIX_WORKING_DIR` | `~/.phoenix` | Data storage location |
| `PHOENIX_ENABLE_AUTH` | false | Enable auth for dashboard |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:6006` | Where spans are sent |

## File Map

| File | Purpose |
|------|---------|
| `atlas/core/observability/phoenix.py` | Phoenix client initialization + span helpers |
| `atlas/core/observability/evaluators.py` | Custom evaluator definitions |
| `atlas/core/observability/experiments.py` | Teacher-student experiment runner |
| `atlas/core/providers/*.py` | Instrumented provider calls (span creation) |
