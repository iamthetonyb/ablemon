# ABLE Distillation Pipeline

> Building custom Qwen 3.5 models from ABLE's own interaction data.

## Architecture

```
Harvest --> Grade --> Build Corpus --> Train (H100) --> Quantize --> Validate --> Deploy
```

The pipeline turns high-quality ABLE interactions into fine-tuning data, trains custom local models, and promotes them through the routing tiers.

## Data Flow

### 1. Conversation Harvesters

Multiple sources feed raw conversation data into the pipeline:

- **Claude Code**: Parses session JSONL from `~/.claude/`
- **ABLE Interactions**: High-quality responses from `data/interaction_log.db` (flagged via `corpus_eligible`)
- **Inbox**: Manual uploads to `~/able-corpus-inbox/`
- **OpenCLI**: Multi-platform harvesting (ChatGPT, Codex, Grok, etc.)

Each harvester normalizes its source format into a common intermediate representation before grading.

### 2. Training Formatter

Standardizes all harvested data to ChatML format for Qwen 3.5 fine-tuning:

```
<|im_start|>system
You are ABLE, an autonomous AI agent...
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
- **Versioned output**: `~/.able/distillation/corpus/vNNN/`

### 4. Corpus Tiers

| Tier | Size | When |
|------|------|------|
| Seed | 500-2,000 | First training cycle |
| Growth | 2,000-10,000 | Improved training |
| Full | 10,000-50,000 | Comprehensive |

## Student Models

| Model | Base | Quant | Size | Role |
|-------|------|-------|------|------|
| **able-gemma4-e4b** | Gemma 4 E4B | GGUF (Unsloth) | ~5GB | **Primary** — fits free Colab T4, Apache 2.0 |
| able-nano-9b | Qwen 3.5 9B | UD-IQ2_M | 3.65GB | Edge/Mobile |
| able-nano-9b-balanced | Qwen 3.5 9B | UD-Q4_K_XL | 5.97GB | Balanced Edge |

Gemma 4 E4B (5.1B params) is the primary distillation target — smallest, fastest, fits free Colab T4 (8GB VRAM). The 9B models target edge devices where VRAM and storage are constrained.

**Safety**: Gemma 4 requires Unsloth (CUDA) for training. Vanilla PEFT + gradient checkpointing causes KV-sharing corruption.

## Training Pipeline

### QLoRA Configuration

| Parameter | E4B (primary) | 9B |
|-----------|---------------|-----|
| LoRA rank (r) | 16 | 16 |
| LoRA alpha | 16 | 32 |
| Sequence length | 4096 | 4096 |
| Quantization | QLoRA 4-bit | QLoRA 4-bit |
| Template | Gemma 4 (`<start_of_turn>`) | ChatML |
| Masking | train_on_responses_only | train_on_responses_only |
| Framework | Unsloth (mandatory) | Unsloth or vanilla PEFT |

### GPU Budget

- Free Colab T4 (15GB VRAM) — primary training target
- E4B: ~0.2h per 1K training examples on T4
- 9B: ~0.3h per 1K training examples on T4

### Preflight Check

Before training, verify corpus readiness:

```bash
python -m able.core.distillation.training --check
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

Run the standard ABLE eval configs against the student model:
- Domain coverage (code, security, copy, research, planning)
- Tool use accuracy
- Skill adherence (follows SKILL.md instructions)

```bash
python -m able.core.distillation.training --validate --stage eval
```

### Stage 2: Teacher-Student Comparison

Side-by-side comparison with the teacher model (Opus 4.6 or GPT 5.4):
- Quality delta must be within tolerance
- Hallucination rate must not increase
- Response format compliance

```bash
python -m able.core.distillation.training --validate --stage comparison
```

### Stage 3: Security Red Team

Run 67+ attack vectors against the student:
- Prompt injection resistance
- Jailbreak resistance
- Secret leakage prevention
- Instruction following under adversarial conditions

```bash
python -m able.core.distillation.training --validate --stage security
```

### Stage 4: Regression Check

Compare against the previous student model version:
- No capability loss on previously passing tests
- Latency within acceptable bounds
- Memory usage within VRAM budget

```bash
python -m able.core.distillation.training --validate --stage regression
```

### Full Validation

```bash
python -m able.core.distillation.training --validate --stage all
```

## Deployment

After validation passes all 4 stages:

1. Register the fine-tuned model in Ollama
2. Deploy to Tier 5 (offline) for shadow testing
3. After shadow period with no regressions, promote to Tier 0 (if enabled)
4. Monitor via interaction log and Phoenix observability

### Ollama Registration

```bash
# Create Ollama model from fine-tuned E4B GGUF
ollama create able-gemma4-e4b -f config/ollama/Modelfile.e4b

# Verify model loads
ollama run able-gemma4-e4b "Hello, are you ABLE?"
```

## CLI Commands

```bash
# Corpus readiness check
python -m able.core.distillation.training --check

# Full training cycle (all models)
python -m able.core.distillation.training --train all

# Train specific model
python -m able.core.distillation.training --train e4b
python -m able.core.distillation.training --train 9b

# GPU budget report
python -m able.core.distillation.training --budget

# Status of all model versions
python -m able.core.distillation.training --status

# Validate a trained model
python -m able.core.distillation.training --validate --stage all

# Export corpus for external training
python -m able.core.distillation.training --export --format jsonl
```

## Ollama Setup (Base Models)

```bash
# After Colab fine-tune, download E4B GGUF and register:
ollama create able-gemma4-e4b -f config/ollama/Modelfile.e4b

# Fallback: Qwen 3.5 9B for edge deployment
huggingface-cli download unsloth/Qwen3.5-9B-GGUF \
    Qwen3.5-9B-UD-IQ2_M.gguf --local-dir ./models
ollama create qwen3.5-9b-edge -f config/ollama/Modelfile.9b-edge
```

## Current State (2026-04-11)

- 684 total pairs, corpus v048: 165 domain-balanced training pairs
- Primary model: **Gemma 4 E4B** (5.1B params, fits free Colab T4)
- Training tooling: 4 Colab notebooks, 4 local notebooks, 4 standalone trainers, 4 MLX scripts
- **Next step**: Run `notebooks/unsloth_finetune_able-gemma4-e4b.ipynb` on Colab T4

## File Map

| File | Purpose |
|------|---------|
| `config/routing_config.yaml` | Tier 5 Ollama provider definitions |
| `config/ollama/Modelfile.e4b` | Ollama config for E4B primary model |
| `config/ollama/Modelfile.9b-edge` | Ollama config for 9B edge model |
| `config/scorer_weights.yaml` | Tier thresholds (Tier 0 when enabled) |
| `data/interaction_log.db` | Source for ABLE harvester |
| `data/distillation_*.jsonl` | Exported training pairs |
| `notebooks/unsloth_finetune_able-gemma4-e4b.ipynb` | Primary Colab training notebook |
