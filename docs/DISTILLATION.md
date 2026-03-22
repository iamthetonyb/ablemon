# ATLAS Distillation Pipeline

> Building custom Qwen 3.5 models from ATLAS's own interaction data.

## Architecture

```
Harvest --> Grade --> Build Corpus --> Train (H100) --> Quantize --> Validate --> Deploy
```

The pipeline turns high-quality ATLAS interactions into fine-tuning data, trains custom local models, and promotes them through the routing tiers.

## Data Flow

### 1. Conversation Harvesters

Multiple sources feed raw conversation data into the pipeline:

- **Claude Code**: Parses session JSONL from `~/.claude/`
- **ABLE Interactions**: High-quality responses from `data/interaction_log.db` (flagged via `corpus_eligible`)
- **Inbox**: Manual uploads to `~/atlas-corpus-inbox/`
- **OpenCLI**: Multi-platform harvesting (ChatGPT, Codex, Grok, etc.)

Each harvester normalizes its source format into a common intermediate representation before grading.

### 2. Training Formatter

Standardizes all harvested data to ChatML format for Qwen 3.5 fine-tuning:

```
<|im_start|>system
You are ATLAS, an autonomous AI agent...
<|im_end|>
<|im_start|>user
{user_message}
<|im_end|>
<|im_start|>assistant
{assistant_response}
<|im_end|>
```

Key behaviors:
- Preserves thinking tokens for chain-of-thought distillation
- Tags for `train_on_responses_only` masking (the model learns to generate responses, not echo user messages)
- Strips PII and secrets before formatting

### 3. Corpus Builder

Assembles graded, formatted data into versioned training corpora:

- **Quality filtering**: Only responses scoring >= 0.8 are included
- **Deduplication**: Content hash prevents cross-platform duplicates
- **Domain balancing**: Max 30% of corpus from any single domain
- **Per-tenant isolation**: Tenant data never leaks into other tenants' corpora
- **Versioned output**: `~/.atlas/distillation/corpus/vNNN/`

### 4. Corpus Tiers

| Tier | Size | When |
|------|------|------|
| Seed | 500-2,000 | First training cycle |
| Growth | 2,000-10,000 | Improved training |
| Full | 10,000-50,000 | Comprehensive |

## Dual Student Models

| Model | Base | Quant | Size | Role |
|-------|------|-------|------|------|
| atlas-student-27b | Qwen 3.5 27B | UD-Q4_K_XL | 17.6GB | Server |
| atlas-nano-9b | Qwen 3.5 9B | UD-IQ2_M | 3.65GB | Edge/Mobile |
| atlas-nano-9b-balanced | Qwen 3.5 9B | UD-Q4_K_XL | 5.97GB | Balanced Edge |

The 27B model targets server deployment as a T0/T1 replacement. The 9B models target edge devices where VRAM and storage are constrained.

## Training Pipeline

### QLoRA Configuration

| Parameter | 27B | 9B |
|-----------|-----|-----|
| LoRA rank (r) | 32 | 16 |
| LoRA alpha | 64 | 32 |
| Sequence length | 8192 | 4096 |
| Quantization | QLoRA 4-bit | QLoRA 4-bit |
| Template | ChatML | ChatML |
| Masking | train_on_responses_only | train_on_responses_only |

### GPU Budget

- 20h/month H100 80GB (Colab)
- ~8h core model training, ~12h tenant-specific training, 2.5h buffer
- 27B: ~0.9h per 1K training examples
- 9B: ~0.3h per 1K training examples

### Preflight Check

Before training, verify corpus readiness:

```bash
python -m atlas.core.distillation.training --check
```

This validates:
- Minimum corpus size (500 examples for seed tier)
- Domain distribution (no single domain > 30%)
- Format compliance (valid ChatML)
- No duplicate content hashes
- GPU availability and VRAM requirements

## Validation Gate

4-stage validation before any student model is deployed:

### Stage 1: Promptfoo Eval Suite

Run the standard ATLAS eval configs against the student model:
- Domain coverage (code, security, copy, research, planning)
- Tool use accuracy
- Skill adherence (follows SKILL.md instructions)

```bash
python -m atlas.core.distillation.training --validate --stage eval
```

### Stage 2: Teacher-Student Comparison

Side-by-side comparison with the teacher model (Opus 4.6 or GPT 5.4):
- Quality delta must be within tolerance
- Hallucination rate must not increase
- Response format compliance

```bash
python -m atlas.core.distillation.training --validate --stage comparison
```

### Stage 3: Security Red Team

Run 67+ attack vectors against the student:
- Prompt injection resistance
- Jailbreak resistance
- Secret leakage prevention
- Instruction following under adversarial conditions

```bash
python -m atlas.core.distillation.training --validate --stage security
```

### Stage 4: Regression Check

Compare against the previous student model version:
- No capability loss on previously passing tests
- Latency within acceptable bounds
- Memory usage within VRAM budget

```bash
python -m atlas.core.distillation.training --validate --stage regression
```

### Full Validation

```bash
python -m atlas.core.distillation.training --validate --stage all
```

## Deployment

After validation passes all 4 stages:

1. Register the fine-tuned model in Ollama
2. Deploy to Tier 5 (offline) for shadow testing
3. After shadow period with no regressions, promote to Tier 0 (if enabled)
4. Monitor via interaction log and Phoenix observability

### Ollama Registration

```bash
# Create Ollama model from fine-tuned GGUF
ollama create atlas-student-27b -f config/ollama/Modelfile.student-27b

# Verify model loads
ollama run atlas-student-27b "Hello, are you ATLAS?"
```

## CLI Commands

```bash
# Corpus readiness check
python -m atlas.core.distillation.training --check

# Full training cycle (all models)
python -m atlas.core.distillation.training --train all

# Train specific model
python -m atlas.core.distillation.training --train 27b
python -m atlas.core.distillation.training --train 9b

# GPU budget report
python -m atlas.core.distillation.training --budget

# Status of all model versions
python -m atlas.core.distillation.training --status

# Validate a trained model
python -m atlas.core.distillation.training --validate --stage all

# Export corpus for external training
python -m atlas.core.distillation.training --export --format jsonl
```

## Ollama Setup (Base Models)

```bash
# Download GGUFs from HuggingFace
huggingface-cli download unsloth/Qwen3.5-27B-GGUF \
    Qwen3.5-27B-UD-Q4_K_XL.gguf --local-dir ./models
huggingface-cli download unsloth/Qwen3.5-9B-GGUF \
    Qwen3.5-9B-UD-IQ2_M.gguf --local-dir ./models
huggingface-cli download unsloth/Qwen3.5-9B-GGUF \
    Qwen3.5-9B-UD-Q4_K_XL.gguf --local-dir ./models

# Create Ollama models from base GGUFs
ollama create qwen3.5-27b-ud -f config/ollama/Modelfile.27b
ollama create qwen3.5-9b-edge -f config/ollama/Modelfile.9b-edge
ollama create qwen3.5-9b-balanced -f config/ollama/Modelfile.9b-balanced
```

## Current State

- ~20 training pairs collected, targeting 500 for seed tier
- H100 cluster access: ~10-20 hours per Colab session
- Schedule: weekly/bi-weekly fine-tuning after data accumulation
- Base models: Qwen 3.5 27B + 9B with Unsloth Dynamic 2.0 quants
- Modelfiles: `config/ollama/Modelfile.{27b,9b-edge,9b-balanced}`

## File Map

| File | Purpose |
|------|---------|
| `config/routing_config.yaml` | Tier 5 Ollama provider definitions |
| `config/ollama/Modelfile.27b` | Ollama config for 27B server model |
| `config/ollama/Modelfile.9b-edge` | Ollama config for 9B edge model |
| `config/ollama/Modelfile.9b-balanced` | Ollama config for 9B balanced model |
| `config/scorer_weights.yaml` | Tier thresholds (Tier 0 when enabled) |
| `data/interaction_log.db` | Source for ABLE harvester |
| `data/distillation_*.jsonl` | Exported training pairs |
