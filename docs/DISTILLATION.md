# Distillation Pipeline

## Overview

The distillation pipeline builds custom local models by harvesting high-quality outputs from premium providers (T4 Opus, T2 GPT 5.4) and training Qwen 3.5 base models to replicate that quality locally.

```
Harvesters → Corpus → Training (H100) → Validation → Deployment (Ollama T5)
```

The end goal: replace paid T1/T2 API calls with free local models that produce equivalent output.

## Pipeline Stages

### 1. Harvest

Interaction logger captures every routed request. Harvesters filter for training-eligible pairs:

- **Eval harvester**: Extracts gold (T4) vs student (T1/T5) pairs from eval runs
- **Interaction harvester**: Pulls high-quality completions from `data/interaction_log.db` where `success=1` and `user_correction=0`
- **Skill harvester**: Captures skill-specific outputs (copywriting, code, security) with domain tags

Eligibility criteria (set in interaction logger):
- `corpus_eligible: true` — not filtered by PII, length, or error
- `raw_input` and `raw_output` stored for training pairs
- Minimum output length threshold (>50 tokens)
- No error states, no fallback chains

### 2. Corpus

Training data accumulates in `data/distillation_*.jsonl` files, one per domain.

| Tier | Pairs | When to Train |
|------|-------|---------------|
| Seed | 500-2,000 | First fine-tune run — enough for measurable improvement |
| Growth | 2,000-10,000 | Weekly runs — domain specialization emerges |
| Full | 10,000-50,000 | Bi-weekly — approaching ceiling of base model capacity |

**Current state**: ~20 pairs collected. Target: 500+ before first H100 run.

Format: ChatML JSONL with system/user/assistant turns:
```jsonl
{"messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]}
```

### 3. Training (H100)

QLoRA fine-tuning via Axolotl on H100 cluster (Colab).

| Parameter | Value |
|-----------|-------|
| Method | QLoRA (4-bit base + LoRA adapters) |
| Framework | Axolotl |
| Template | ChatML (`<|im_start|>system/user/assistant`) |
| Training mode | `train_on_responses_only: true` |
| LoRA rank | 64 |
| LoRA alpha | 128 |
| Learning rate | 2e-4 |
| Epochs | 3-5 (early stopping on val loss) |
| Batch size | 4 (gradient accumulation 8) |

### 4. Quantization

Post-training, re-quantize to Unsloth Dynamic 2.0 targets:

| Target | Base | Quant | Size | Use Case |
|--------|------|-------|------|----------|
| Server | Qwen 3.5 27B | UD-Q4_K_XL | 17.6GB | Primary local T1 replacement |
| Edge (primary) | Qwen 3.5 9B | UD-IQ2_M | 3.65GB | Mobile/offline deployment |
| Edge (balanced) | Qwen 3.5 9B | UD-Q4_K_XL | 5.97GB | When device has more room |

Unsloth Dynamic 2.0 quants preserve accuracy better than standard GGUF quants at the same bit width.

### 5. Validation Gate

4-stage validation before any fine-tuned model replaces a production provider:

| Stage | Gate | Pass Criteria |
|-------|------|---------------|
| Eval | Run full eval suite against fine-tuned model | >= 80% of T4 gold score |
| Comparison | A/B test fine-tuned vs current T5 on 50 prompts | Win rate > 60% |
| Red-team | Security + edge case prompts | No regressions vs base model |
| Regression | Re-run last 3 eval configs | No score drops > 5% on any domain |

### 6. Deployment

Register validated model in Ollama and swap into T5:

```bash
# Create Ollama model from fine-tuned GGUF
ollama create atlas-qwen-27b-v1 -f config/ollama/Modelfile.27b-finetuned

# Update routing_config.yaml: ollama-local model_id → atlas-qwen-27b-v1
# Run validation suite
# If passing, promote: T5 → T1 candidate
```

## H100 Budget

20 hours/month total allocation:

| Allocation | Hours | Purpose |
|------------|-------|---------|
| Core model | 8h | Primary 27B + 9B fine-tuning |
| Tenant models | 10h | Per-tenant fine-tuning (multi-tenant) |
| Buffer | 2h | Re-runs, experiments |

Schedule: weekly or bi-weekly sessions via Colab, depending on corpus growth rate.

## Model Configs

### Ollama Modelfiles

| File | Model |
|------|-------|
| `config/ollama/Modelfile.27b` | Qwen 3.5 27B UD-Q4_K_XL (server) |
| `config/ollama/Modelfile.9b-edge` | Qwen 3.5 9B UD-IQ2_M (edge, 3.65GB) |
| `config/ollama/Modelfile.9b-balanced` | Qwen 3.5 9B UD-Q4_K_XL (edge, 5.97GB) |

### Downloading Base GGUFs

```bash
huggingface-cli download unsloth/Qwen3.5-27B-GGUF Qwen3.5-27B-UD-Q4_K_XL.gguf --local-dir ./models
huggingface-cli download unsloth/Qwen3.5-9B-GGUF Qwen3.5-9B-UD-IQ2_M.gguf --local-dir ./models
huggingface-cli download unsloth/Qwen3.5-9B-GGUF Qwen3.5-9B-UD-Q4_K_XL.gguf --local-dir ./models
```

### Creating Ollama Models

```bash
ollama create qwen3.5-27b-ud -f config/ollama/Modelfile.27b
ollama create qwen3.5-9b-edge -f config/ollama/Modelfile.9b-edge
ollama create qwen3.5-9b-balanced -f config/ollama/Modelfile.9b-balanced
```

## CLI Commands

```bash
# Export training corpus from interaction log
python -m atlas.core.distillation.export --domain all --min-quality 0.8 --output data/distillation_all.jsonl

# Check corpus stats
python -m atlas.core.distillation.stats

# Validate a fine-tuned model against eval suite
python -m atlas.core.distillation.validate --model path/to/model.gguf --eval-config atlas/evals/

# Register fine-tuned model in Ollama
python -m atlas.core.distillation.deploy --model atlas-qwen-27b-v1 --tier 5
```

## File Map

| File | Purpose |
|------|---------|
| `config/ollama/Modelfile.27b` | Server model config |
| `config/ollama/Modelfile.9b-edge` | Edge model config (compact) |
| `config/ollama/Modelfile.9b-balanced` | Edge model config (balanced) |
| `config/routing_config.yaml` | Provider registry (T5 entries) |
| `data/distillation_*.jsonl` | Training corpus files |
| `data/interaction_log.db` | Source data for harvesting |
