"""Unsloth training exporter — generates Colab-ready notebooks and scripts.

Bridges the federation corpus → Unsloth fine-tuning → GGUF export → Ollama T5
pipeline. Designed to maximize the free Colab T4 runtime (12-24 hours).

Usage:
    from able.core.distillation.training.unsloth_exporter import UnslothExporter
    exporter = UnslothExporter()
    exporter.export_notebook("able-nano-9b", "data/corpus/default/v001/train.jsonl")
    # → generates notebooks/unsloth_finetune_able-nano-9b.ipynb

The generated notebook:
1. Installs Unsloth in Colab
2. Loads the corpus from Google Drive (or uploaded file)
3. Fine-tunes with LoRA using Unsloth's 2x speed + 70% VRAM savings
4. Exports to GGUF (Dynamic 2.0 quants: UD-Q4_K_XL, UD-IQ2_M)
5. Pushes GGUF to HuggingFace Hub for Ollama download
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from able.core.distillation.training.model_configs import (
    MODEL_REGISTRY,
    StudentModelConfig,
)

logger = logging.getLogger(__name__)

_DEFAULT_OUTPUT = Path("notebooks")

# Unsloth-specific LoRA config (2x faster than vanilla PEFT)
_UNSLOTH_LORA_DEFAULTS = {
    "able-nano-9b": {
        "r": 16,
        "target_modules": [
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
        "lora_alpha": 16,
        "lora_dropout": 0,
        "use_gradient_checkpointing": "unsloth",
        "max_seq_length": 2048,
        "load_in_4bit": True,
    },
    "able-student-27b": {
        "r": 32,
        "target_modules": [
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
        "lora_alpha": 32,
        "lora_dropout": 0,
        "use_gradient_checkpointing": "unsloth",
        "max_seq_length": 4096,
        "load_in_4bit": True,
    },
}

# GGUF quant targets per model
_GGUF_QUANTS = {
    "able-nano-9b": ["q4_k_m", "iq2_m", "q8_0"],
    "able-student-27b": ["q4_k_m", "q5_k_m", "q8_0"],
}


class UnslothExporter:
    """Generate Unsloth fine-tuning notebooks and training scripts."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or _DEFAULT_OUTPUT

    def export_notebook(
        self,
        model_name: str,
        corpus_path: str,
        hf_repo: Optional[str] = None,
        epochs: int = 3,
        runtime: str = "t4_colab",
    ) -> Path:
        """Generate a Colab-ready Jupyter notebook for fine-tuning.

        Args:
            model_name: Key from MODEL_REGISTRY (e.g., "able-nano-9b").
            corpus_path: Path to training JSONL (ChatML format).
            hf_repo: HuggingFace repo to push GGUF exports to.
            epochs: Training epochs.
            runtime: GPU runtime ("t4_colab", "a100_session", "l4_session",
                     "h100_session", "local").

        Returns:
            Path to the generated .ipynb file.
        """
        config = MODEL_REGISTRY.get(model_name)
        if not config:
            raise ValueError(f"Unknown model: {model_name}. Available: {list(MODEL_REGISTRY)}")

        lora_config = _UNSLOTH_LORA_DEFAULTS.get(model_name, _UNSLOTH_LORA_DEFAULTS["able-nano-9b"])
        quants = _GGUF_QUANTS.get(model_name, ["q4_k_m"])
        runtime_profile = config.runtime_profiles.get(runtime, {})

        cells = []

        # Cell 1: Title + setup
        cells.append(self._markdown_cell(
            f"# ABLE Distillation: Fine-tune {config.base_model}\n\n"
            f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
            f"**Model**: {config.base_model} ({config.role})\n"
            f"**Corpus**: `{corpus_path}`\n"
            f"**Runtime**: {runtime}\n"
            f"**Epochs**: {epochs}\n\n"
            f"This notebook uses Unsloth for 2x faster training with 70% less VRAM.\n"
            f"Free Colab T4 runtime: 12-24 hours available."
        ))

        # Cell 2: Install Unsloth
        cells.append(self._code_cell(
            "# Install Unsloth (takes ~2 minutes on Colab)\n"
            "%%capture\n"
            "!pip install unsloth\n"
            "!pip install --no-deps trl peft accelerate bitsandbytes"
        ))

        # Cell 3: Load model with Unsloth
        cells.append(self._code_cell(
            "from unsloth import FastLanguageModel\n"
            "import torch\n\n"
            f"model, tokenizer = FastLanguageModel.from_pretrained(\n"
            f'    model_name="{config.base_model}",\n'
            f"    max_seq_length={lora_config['max_seq_length']},\n"
            f"    load_in_4bit={lora_config['load_in_4bit']},\n"
            f'    dtype=None,  # Auto-detect (float16 on T4, bfloat16 on A100/H100)\n'
            f")\n\n"
            f"model = FastLanguageModel.get_peft_model(\n"
            f"    model,\n"
            f"    r={lora_config['r']},\n"
            f"    target_modules={lora_config['target_modules']},\n"
            f"    lora_alpha={lora_config['lora_alpha']},\n"
            f"    lora_dropout={lora_config['lora_dropout']},\n"
            f'    use_gradient_checkpointing="{lora_config["use_gradient_checkpointing"]}",\n'
            f")\n\n"
            f'print(f"Model loaded: {{model.get_nb_trainable_parameters()}} trainable params")'
        ))

        # Cell 4: Load and format corpus
        cells.append(self._code_cell(
            "from datasets import load_dataset\n"
            "import json\n\n"
            "# Load ABLE distillation corpus (ChatML format)\n"
            "# Upload train.jsonl to Colab or mount Google Drive\n"
            f'CORPUS_PATH = "{corpus_path}"\n\n'
            "def format_chatml(example):\n"
            '    """Format a single training example as ChatML."""\n'
            "    messages = example.get('messages', [])\n"
            "    formatted = tokenizer.apply_chat_template(\n"
            "        messages, tokenize=False, add_generation_prompt=False\n"
            "    )\n"
            '    return {"text": formatted}\n\n'
            'dataset = load_dataset("json", data_files=CORPUS_PATH, split="train")\n'
            "dataset = dataset.map(format_chatml)\n"
            f'print(f"Loaded {{len(dataset)}} training examples")'
        ))

        # Cell 5: Training
        lr = runtime_profile.get("learning_rate", config.learning_rate)
        micro_batch = runtime_profile.get("micro_batch_size", config.micro_batch_size)
        grad_accum = runtime_profile.get("gradient_accumulation", config.gradient_accumulation)

        cells.append(self._code_cell(
            "from trl import SFTTrainer\n"
            "from transformers import TrainingArguments\n"
            "from unsloth import is_bfloat16_supported\n\n"
            "trainer = SFTTrainer(\n"
            "    model=model,\n"
            "    tokenizer=tokenizer,\n"
            "    train_dataset=dataset,\n"
            '    dataset_text_field="text",\n'
            f"    max_seq_length={lora_config['max_seq_length']},\n"
            "    dataset_num_proc=2,\n"
            "    args=TrainingArguments(\n"
            f"        per_device_train_batch_size={micro_batch},\n"
            f"        gradient_accumulation_steps={grad_accum},\n"
            f"        num_train_epochs={epochs},\n"
            f"        learning_rate={lr},\n"
            "        fp16=not is_bfloat16_supported(),\n"
            "        bf16=is_bfloat16_supported(),\n"
            "        logging_steps=10,\n"
            f'        save_strategy="{runtime_profile.get("save_strategy", "steps")}",\n'
            f'        save_steps={runtime_profile.get("save_steps", 100)},\n'
            f'        output_dir="outputs/{model_name}",\n'
            "        optim=\"adamw_8bit\",\n"
            "        warmup_steps=5,\n"
            "        weight_decay=0.01,\n"
            "        lr_scheduler_type=\"linear\",\n"
            "        seed=42,\n"
            "    ),\n"
            ")\n\n"
            "trainer_stats = trainer.train()\n"
            "print(trainer_stats)"
        ))

        # Cell 6: Export to GGUF
        quant_list = ", ".join(f'"{q}"' for q in quants)
        hf_target = hf_repo or f"able-distilled/{model_name}"

        cells.append(self._markdown_cell(
            "## Export to GGUF for Ollama\n\n"
            "This exports the fine-tuned model to GGUF format using Unsloth's\n"
            "Dynamic 2.0 quantization for optimal size/quality balance."
        ))

        cells.append(self._code_cell(
            f"# Export to GGUF (Unsloth Dynamic 2.0 quantization)\n"
            f"QUANT_METHODS = [{quant_list}]\n\n"
            f"for quant in QUANT_METHODS:\n"
            f'    print(f"Exporting {{quant}}...")\n'
            f"    model.save_pretrained_gguf(\n"
            f'        f"outputs/{model_name}-gguf",\n'
            f"        tokenizer,\n"
            f"        quantization_method=quant,\n"
            f"    )\n"
            f'    print(f"  Done: outputs/{model_name}-gguf")\n\n'
            f"# Optional: Push to HuggingFace Hub\n"
            f"# model.push_to_hub_gguf(\n"
            f'#     "{hf_target}",\n'
            f"#     tokenizer,\n"
            f"#     quantization_method=QUANT_METHODS[0],\n"
            f'#     token="hf_...",  # Your HF token\n'
            f"# )"
        ))

        # Cell 7: Ollama deployment
        cells.append(self._markdown_cell(
            "## Deploy to Ollama\n\n"
            "After downloading the GGUF file to your local machine:"
        ))

        cells.append(self._code_cell(
            f"# Generate Ollama Modelfile\n"
            f"MODELFILE = '''\n"
            f"FROM ./outputs/{model_name}-gguf/unsloth.Q4_K_M.gguf\n"
            f"TEMPLATE \"\"\"{{{{- if .System }}}}<|im_start|>system\n"
            f"{{{{ .System }}}}<|im_end|>\n"
            f"{{{{- end }}}}<|im_start|>user\n"
            f"{{{{ .Prompt }}}}<|im_end|>\n"
            f"<|im_start|>assistant\n"
            f"{{{{ .Response }}}}<|im_end|>\"\"\"\n"
            f'PARAMETER temperature 0.7\n'
            f'PARAMETER top_p 0.9\n'
            f"PARAMETER stop \"<|im_end|>\"\n"
            f"PARAMETER stop \"<|im_start|>\"\n"
            f"SYSTEM You are ABLE, an autonomous AI agent.\n"
            f"'''\n\n"
            f'with open("Modelfile", "w") as f:\n'
            f"    f.write(MODELFILE)\n\n"
            f'print("Modelfile generated. Run locally:")\n'
            f'print(f"  ollama create {model_name} -f Modelfile")\n'
            f'print(f"  ollama run {model_name}")'
        ))

        # Cell 8: Federation stats
        cells.append(self._markdown_cell(
            "## Training Stats for Federation\n\n"
            "After training, the federation sync will pick up these metrics\n"
            "and share quality improvements across the network."
        ))

        cells.append(self._code_cell(
            "import json\n"
            "from datetime import datetime, timezone\n\n"
            "stats = {\n"
            f'    "model": "{model_name}",\n'
            f'    "base": "{config.base_model}",\n'
            f'    "corpus_path": CORPUS_PATH,\n'
            f"    \"corpus_size\": len(dataset),\n"
            f'    "epochs": {epochs},\n'
            f'    "runtime": "{runtime}",\n'
            f'    "quants": QUANT_METHODS,\n'
            f"    \"loss\": trainer_stats.training_loss,\n"
            f'    "completed_at": datetime.now(timezone.utc).isoformat(),\n'
            "}\n\n"
            f'with open("outputs/{model_name}_training_stats.json", "w") as f:\n'
            f"    json.dump(stats, f, indent=2)\n\n"
            f'print(json.dumps(stats, indent=2))'
        ))

        # Build notebook
        _gpu_type_map = {
            "t4_colab": "T4",
            "l4_session": "L4",
            "a100_session": "A100",
            "h100_session": "A100",  # Colab shows A100 for 80GB too
            "local": "T4",
        }
        notebook = {
            "nbformat": 4,
            "nbformat_minor": 0,
            "metadata": {
                "colab": {
                    "provenance": [],
                    "gpuType": _gpu_type_map.get(runtime, "T4"),
                },
                "kernelspec": {
                    "name": "python3",
                    "display_name": "Python 3",
                },
                "accelerator": "GPU",
            },
            "cells": cells,
        }

        # Write notebook
        self.output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"unsloth_finetune_{model_name}.ipynb"
        filepath = self.output_dir / filename

        with open(filepath, "w") as f:
            json.dump(notebook, f, indent=2)

        logger.info("Generated Unsloth notebook: %s (%d cells)", filepath, len(cells))
        return filepath

    def export_training_script(
        self,
        model_name: str,
        corpus_path: str,
        output_path: Optional[str] = None,
    ) -> Path:
        """Generate a standalone Python training script for VS Code + Colab.

        This script can be run via VS Code connected to a Colab runtime,
        or directly on any machine with a GPU.
        """
        config = MODEL_REGISTRY.get(model_name)
        if not config:
            raise ValueError(f"Unknown model: {model_name}")

        lora = _UNSLOTH_LORA_DEFAULTS.get(model_name, _UNSLOTH_LORA_DEFAULTS["able-nano-9b"])
        quants = _GGUF_QUANTS.get(model_name, ["q4_k_m"])

        script = f'''#!/usr/bin/env python3
"""ABLE distillation training script — Unsloth + {config.base_model}

Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
Model: {config.base_model} ({config.role})
Corpus: {corpus_path}

Run via VS Code connected to Colab runtime, or directly with a GPU:
    python train_{model_name}.py
"""

from unsloth import FastLanguageModel, is_bfloat16_supported
from datasets import load_dataset
from trl import SFTTrainer
from transformers import TrainingArguments
import json
from datetime import datetime, timezone

# ── Config ─────────────────────────────────────────────────────
MODEL_NAME = "{config.base_model}"
CORPUS_PATH = "{corpus_path}"
MAX_SEQ_LENGTH = {lora["max_seq_length"]}
LORA_R = {lora["r"]}
LORA_ALPHA = {lora["lora_alpha"]}
EPOCHS = 3
BATCH_SIZE = {config.micro_batch_size}
GRAD_ACCUM = {config.gradient_accumulation}
LR = {config.learning_rate}
QUANTS = {quants}

# ── Load model ─────────────────────────────────────────────────
print(f"Loading {{MODEL_NAME}}...")
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name=MODEL_NAME,
    max_seq_length=MAX_SEQ_LENGTH,
    load_in_4bit=True,
    dtype=None,
)

model = FastLanguageModel.get_peft_model(
    model,
    r=LORA_R,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                     "gate_proj", "up_proj", "down_proj"],
    lora_alpha=LORA_ALPHA,
    lora_dropout=0,
    use_gradient_checkpointing="unsloth",
)
print(f"Trainable params: {{model.get_nb_trainable_parameters()}}")

# ── Load corpus ────────────────────────────────────────────────
def format_chatml(example):
    messages = example.get("messages", [])
    return {{"text": tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=False
    )}}

dataset = load_dataset("json", data_files=CORPUS_PATH, split="train")
dataset = dataset.map(format_chatml)
print(f"Loaded {{len(dataset)}} training examples")

# ── Train ──────────────────────────────────────────────────────
trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=dataset,
    dataset_text_field="text",
    max_seq_length=MAX_SEQ_LENGTH,
    dataset_num_proc=2,
    args=TrainingArguments(
        per_device_train_batch_size=BATCH_SIZE,
        gradient_accumulation_steps=GRAD_ACCUM,
        num_train_epochs=EPOCHS,
        learning_rate=LR,
        fp16=not is_bfloat16_supported(),
        bf16=is_bfloat16_supported(),
        logging_steps=10,
        save_strategy="steps",
        save_steps=100,
        output_dir=f"outputs/{model_name}",
        optim="adamw_8bit",
        warmup_steps=5,
        weight_decay=0.01,
        lr_scheduler_type="linear",
        seed=42,
    ),
)

stats = trainer.train()
print(f"Training complete: loss={{stats.training_loss:.4f}}")

# ── Export GGUF ────────────────────────────────────────────────
for quant in QUANTS:
    print(f"Exporting {{quant}}...")
    model.save_pretrained_gguf(
        f"outputs/{model_name}-gguf",
        tokenizer,
        quantization_method=quant,
    )

# ── Save stats ─────────────────────────────────────────────────
training_stats = {{
    "model": "{model_name}",
    "base": MODEL_NAME,
    "corpus_size": len(dataset),
    "epochs": EPOCHS,
    "loss": stats.training_loss,
    "quants": QUANTS,
    "completed_at": datetime.now(timezone.utc).isoformat(),
}}
with open(f"outputs/{model_name}_training_stats.json", "w") as f:
    json.dump(training_stats, f, indent=2)

print(json.dumps(training_stats, indent=2))
print(f"\\nGGUF files in outputs/{model_name}-gguf/")
print(f"Create Ollama model: ollama create {model_name} -f Modelfile")
'''

        out = Path(output_path) if output_path else self.output_dir
        out.mkdir(parents=True, exist_ok=True)
        filepath = out / f"train_{model_name}.py"
        filepath.write_text(script)

        logger.info("Generated training script: %s", filepath)
        return filepath

    def export_mlx_training_script(
        self,
        model_name: str,
        corpus_path: str,
        output_path: Optional[str] = None,
        iters: int = 600,
        batch_size: int = 1,
        num_layers: int = 8,
    ) -> Path:
        """Generate a local MLX LoRA training script for Apple Silicon.

        MLX fine-tuning runs entirely on-device using unified memory.
        The 9B model at 4-bit fits comfortably on 36GB Macs. 27B is too
        large for most Apple Silicon configs (needs ~40GB+ for 4-bit QLoRA).

        The generated script:
        1. Trains a LoRA adapter via ``mlx_lm.lora``
        2. Fuses the adapter into the base model
        3. Converts to GGUF via llama.cpp for Ollama import

        Args:
            model_name: Key from MODEL_REGISTRY.
            corpus_path: Path to training JSONL (ChatML messages format).
            output_path: Directory for the generated script.
            iters: Training iterations (default 600).
            batch_size: Per-device batch size (1 for 32GB, 2 for 64GB+).
            num_layers: Number of layers to fine-tune (fewer = less memory).
        """
        config = MODEL_REGISTRY.get(model_name)
        if not config:
            raise ValueError(f"Unknown model: {model_name}")

        lora = _UNSLOTH_LORA_DEFAULTS.get(model_name, _UNSLOTH_LORA_DEFAULTS["able-nano-9b"])

        # MLX expects a 4-bit quantized model ID or local path
        mlx_model = f"{config.base_model}-4bit"

        script = f'''#!/usr/bin/env bash
# ABLE Distillation — Local MLX LoRA Fine-Tuning
# Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
# Model: {config.base_model} ({config.role})
# Runtime: Apple Silicon MLX (unified memory)
#
# Requirements:
#   pip install "mlx-lm[train]"
#   The corpus must be ChatML JSONL with "messages" field.
#
# Memory: 9B 4-bit needs ~8-10GB for training. 36GB Mac = comfortable.
#         27B 4-bit needs ~20-24GB — only for 64GB+ Macs.

set -euo pipefail

MODEL="{mlx_model}"
CORPUS_DIR="$(dirname "{corpus_path}")"
ADAPTER_DIR="adapters/{model_name}"
FUSED_DIR="fused/{model_name}"
GGUF_DIR="gguf/{model_name}"

echo "══════════════════════════════════════════════════════════"
echo "  ABLE MLX LoRA Training: {config.base_model}"
echo "  Corpus: {corpus_path}"
echo "  Iterations: {iters} | Batch: {batch_size} | Layers: {num_layers}"
echo "══════════════════════════════════════════════════════════"

# ── Step 1: Install dependencies ──────────────────────────────
pip install -q "mlx-lm[train]" 2>/dev/null || true

# ── Step 2: Train LoRA adapter ────────────────────────────────
echo ""
echo "▸ Training LoRA adapter..."
python3 -m mlx_lm.lora \\
    --model "$MODEL" \\
    --train \\
    --data "$CORPUS_DIR" \\
    --adapter-path "$ADAPTER_DIR" \\
    --batch-size {batch_size} \\
    --num-layers {num_layers} \\
    --lora-rank {lora["r"]} \\
    --iters {iters} \\
    --grad-checkpoint \\
    --mask-prompt

echo "▸ Adapter saved to $ADAPTER_DIR"

# ── Step 3: Evaluate (optional) ──────────────────────────────
if [ -f "$CORPUS_DIR/valid.jsonl" ]; then
    echo ""
    echo "▸ Evaluating on validation set..."
    python3 -m mlx_lm.lora \\
        --model "$MODEL" \\
        --adapter-path "$ADAPTER_DIR" \\
        --data "$CORPUS_DIR" \\
        --test
fi

# ── Step 4: Fuse adapter into base model ─────────────────────
echo ""
echo "▸ Fusing adapter..."
python3 -m mlx_lm.fuse \\
    --model "$MODEL" \\
    --adapter-path "$ADAPTER_DIR" \\
    --save-path "$FUSED_DIR"

echo "▸ Fused model at $FUSED_DIR"

# ── Step 5: Convert to GGUF for Ollama ───────────────────────
# Qwen is not in mlx-lm's native GGUF exporter, so we use llama.cpp.
echo ""
echo "▸ Converting to GGUF..."
mkdir -p "$GGUF_DIR"

if command -v python3 -c "import llama_cpp" &>/dev/null || [ -d "llama.cpp" ]; then
    # If llama.cpp is available locally
    python3 llama.cpp/convert_hf_to_gguf.py "$FUSED_DIR" \\
        --outfile "$GGUF_DIR/{model_name}-f16.gguf" \\
        --outtype f16
    echo "▸ GGUF exported: $GGUF_DIR/{model_name}-f16.gguf"
    echo ""
    echo "  To quantize further:"
    echo "    llama.cpp/llama-quantize $GGUF_DIR/{model_name}-f16.gguf $GGUF_DIR/{model_name}-q4_k_m.gguf q4_k_m"
else
    echo "▸ llama.cpp not found. Clone it for GGUF conversion:"
    echo "    git clone https://github.com/ggml-org/llama.cpp"
    echo "    pip install -r llama.cpp/requirements.txt"
    echo "    python3 llama.cpp/convert_hf_to_gguf.py $FUSED_DIR --outfile $GGUF_DIR/{model_name}-f16.gguf --outtype f16"
fi

# ── Step 6: Register in Ollama ───────────────────────────────
echo ""
echo "▸ To deploy in Ollama:"
echo "    cat > Modelfile <<MODELFILE"
echo "FROM ./$GGUF_DIR/{model_name}-q4_k_m.gguf"
echo "TEMPLATE \\"{{{{- if .System }}}}<|im_start|>system"
echo "{{{{ .System }}}}<|im_end|>"
echo "{{{{- end }}}}<|im_start|>user"
echo "{{{{ .Prompt }}}}<|im_end|>"
echo "<|im_start|>assistant"
echo "{{{{ .Response }}}}<|im_end|>\\""
echo "PARAMETER temperature 0.7"
echo "PARAMETER stop \\"<|im_end|>\\""
echo "SYSTEM You are ABLE, an autonomous AI agent."
echo "MODELFILE"
echo ""
echo "    ollama create {model_name}-mlx -f Modelfile"
echo "    ollama run {model_name}-mlx"

echo ""
echo "══════════════════════════════════════════════════════════"
echo "  Training complete. Adapter: $ADAPTER_DIR"
echo "  Fused model: $FUSED_DIR"
echo "══════════════════════════════════════════════════════════"
'''

        out = Path(output_path) if output_path else self.output_dir
        out.mkdir(parents=True, exist_ok=True)
        filepath = out / f"train_mlx_{model_name}.sh"
        filepath.write_text(script)
        filepath.chmod(0o755)

        logger.info("Generated MLX training script: %s", filepath)
        return filepath

    # ── Notebook cell helpers ─────────────────────────────────────

    @staticmethod
    def _markdown_cell(source: str) -> dict:
        return {
            "cell_type": "markdown",
            "metadata": {},
            "source": [source],
        }

    @staticmethod
    def _code_cell(source: str) -> dict:
        return {
            "cell_type": "code",
            "metadata": {},
            "source": [source],
            "outputs": [],
            "execution_count": None,
        }
