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
    # Gemma 4 models — r=8, alpha=8 per Unsloth docs
    # WARNING: use_cache=False with gradient checkpointing causes KV-sharing
    # corruption on Gemma 4 E2B/E4B. MUST use Unsloth's fix.
    "able-gemma4-31b": {
        "r": 8,
        "target_modules": [
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
        "lora_alpha": 8,
        "lora_dropout": 0,
        "use_gradient_checkpointing": "unsloth",
        "max_seq_length": 8192,
        "load_in_4bit": True,
        "finetune_vision_layers": False,
        "finetune_language_layers": True,
        "finetune_attention_modules": True,
        "finetune_mlp_modules": True,
    },
    "able-gemma4-e4b": {
        "r": 8,
        "target_modules": [
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
        "lora_alpha": 8,
        "lora_dropout": 0,
        "use_gradient_checkpointing": "unsloth",
        "max_seq_length": 4096,
        "load_in_4bit": True,
        "finetune_vision_layers": False,
        "finetune_language_layers": True,
        "finetune_attention_modules": True,
        "finetune_mlp_modules": True,
    },
}

# GGUF quant targets per model
_GGUF_QUANTS = {
    "able-nano-9b": ["q4_k_m", "iq2_m", "q8_0"],
    "able-student-27b": ["q4_k_m", "q5_k_m", "q8_0"],
    "able-gemma4-31b": ["q4_k_m", "q5_k_m", "q8_0"],
    "able-gemma4-e4b": ["q4_k_m", "iq2_m"],
}

# Gemma 4 uses <|turn> tags, not ChatML. For train_on_responses_only:
#   instruction_part = "<|turn>user\n"
#   response_part = "<|turn>model\n"
# The tokenizer adds <bos> prefix — remove with removeprefix('<bos>')
_GEMMA4_MODELS = {"able-gemma4-31b", "able-gemma4-e4b"}


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

        # Gemma 4 KV-sharing bug guard: use_cache=False + gradient checkpointing
        # corrupts training without Unsloth's fix. All generated notebooks use
        # Unsloth, so this is safe — but warn if someone tries to train outside
        # the generated notebooks.
        if model_name in _GEMMA4_MODELS:
            import warnings
            warnings.warn(
                f"CRITICAL: {model_name} requires Unsloth for training. "
                f"use_cache=False + gradient checkpointing causes KV-sharing "
                f"corruption on Gemma 4 without Unsloth's fix. Do NOT train "
                f"this model with vanilla transformers/PEFT.",
                stacklevel=2,
            )

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
        # %%capture MUST be the first line of the cell for IPython to recognize it
        cells.append(self._code_cell(
            "%%capture\n"
            "# Install Unsloth (takes ~2 minutes on Colab)\n"
            "# Pin >=2026.4.3 for Gemma 4 gradient accumulation fix + Qwen 3.5 stability\n"
            "!pip install 'unsloth>=2026.4.3'\n"
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
            "# Upload train.jsonl to Colab, or mount Google Drive and update path\n"
            f'CORPUS_PATH = "train.jsonl"  # Local: {corpus_path}\n\n'
            "def format_chatml(example):\n"
            '    """Format a single training example as ChatML."""\n'
            "    messages = example.get('messages', example.get('conversations', []))\n"
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
            "from unsloth import is_bfloat16_supported\n"
            "import os\n"
            "# Reduce HuggingFace API calls (Studio finding: 90% fewer throttles)\n"
            "os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'\n"
            "os.environ['TRANSFORMERS_NO_ADVISORY_WARNINGS'] = '1'\n\n"
            + (
                "# NOTE: Gemma 4 training loss in range 10-15 is NORMAL.\n"
                "# Do NOT stop training early thinking loss is diverging.\n\n"
                if "gemma" in config.base_model.lower() else ""
            ) +
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
            f"# Full model GGUF export (merged LoRA, not just adapter)\n"
            f"# Requires unsloth >=2026.4.3 — exports merged weights to GGUF\n"
            f"# model.save_pretrained_merged(\n"
            f'#     f"outputs/{model_name}-merged",\n'
            f"#     tokenizer,\n"
            f"#     save_method=\"merged_16bit\",  # or merged_4bit for smaller\n"
            f"# )\n\n"
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

        # Gemma 4 uses start_of_turn/end_of_turn, Qwen uses ChatML
        is_gemma4 = model_name in _GEMMA4_MODELS
        if is_gemma4:
            modelfile_template = (
                f"# Generate Ollama Modelfile (Gemma 4 chat template)\n"
                f"MODELFILE = '''\n"
                f"FROM ./outputs/{model_name}-gguf/unsloth.Q4_K_M.gguf\n"
                f"TEMPLATE \"\"\"{{{{- if .System }}}}<start_of_turn>user\n"
                f"{{{{ .System }}}}<end_of_turn>\n"
                f"{{{{ end }}}}{{{{- range .Messages }}}}<start_of_turn>{{{{ if eq .Role \"assistant\" }}}}model{{{{ else }}}}{{{{ .Role }}}}{{{{ end }}}}\n"
                f"{{{{ .Content }}}}<end_of_turn>\n"
                f"{{{{ end }}}}<start_of_turn>model\n"
                f"\"\"\"\n"
                f'PARAMETER temperature 0.7\n'
                f'PARAMETER flash_attention on\n'
                f'PARAMETER cache_type_k q4_0\n'
                f'PARAMETER cache_type_v q4_0\n'
                f"PARAMETER stop \"<end_of_turn>\"\n"
                f"PARAMETER stop \"<start_of_turn>\"\n"
                f"SYSTEM You are ABLE, an autonomous AI agent.\n"
                f"'''\n\n"
            )
        else:
            modelfile_template = (
                f"# Generate Ollama Modelfile (ChatML template)\n"
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
            )

        cells.append(self._code_cell(
            modelfile_template
            + f'with open("Modelfile", "w") as f:\n'
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
_CORPUS_RAW = "{corpus_path}"
MAX_SEQ_LENGTH = {lora["max_seq_length"]}
LORA_R = {lora["r"]}
LORA_ALPHA = {lora["lora_alpha"]}
EPOCHS = 3
BATCH_SIZE = {config.micro_batch_size}
GRAD_ACCUM = {config.gradient_accumulation}
LR = {config.learning_rate}
QUANTS = {quants}

# ── Resolve corpus path (works from repo root or notebooks/ dir) ──
from pathlib import Path as _P
_corpus = _P(_CORPUS_RAW).expanduser()
if not _corpus.exists():
    _corpus = _P("..") / _CORPUS_RAW  # Running from notebooks/ subdir
if not _corpus.exists():
    import glob as _g
    _candidates = sorted(_g.glob(str(_P.home() / ".able/distillation/corpus/default/v*/train.jsonl")))
    if _candidates:
        _corpus = _P(_candidates[-1])
if not _corpus.exists():
    raise FileNotFoundError(f"Corpus not found: {{_CORPUS_RAW}} — run harvest first or set path manually")
CORPUS_PATH = str(_corpus)
print(f"Corpus: {{CORPUS_PATH}}")

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
    messages = example.get("messages", example.get("conversations", []))
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
        is_gemma4 = model_name in _GEMMA4_MODELS

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
_RAW_CORPUS="{corpus_path}"
# Resolve corpus: try as-is, then parent dir, then ~/.able harvest output
if [ -f "$_RAW_CORPUS" ]; then
    CORPUS_DIR="$(dirname "$_RAW_CORPUS")"
elif [ -f "../$_RAW_CORPUS" ]; then
    CORPUS_DIR="$(dirname "../$_RAW_CORPUS")"
else
    _LATEST=$(ls -d ~/.able/distillation/corpus/default/v*/train.jsonl 2>/dev/null | sort | tail -1)
    if [ -n "$_LATEST" ]; then
        CORPUS_DIR="$(dirname "$_LATEST")"
    else
        echo "ERROR: Corpus not found: $_RAW_CORPUS"
        echo "Run harvest first or set path manually."
        exit 1
    fi
fi
echo "Using corpus: $CORPUS_DIR"
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

if python3 -c "import llama_cpp" &>/dev/null || [ -d "llama.cpp" ]; then
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
''' + (
            # Gemma 4 uses <start_of_turn>/<end_of_turn> template
            f'''echo "TEMPLATE \\"{{{{- if .System }}}}<start_of_turn>user"
echo "{{{{ .System }}}}<end_of_turn>"
echo "{{{{ end }}}}{{{{- range .Messages }}}}<start_of_turn>{{{{ if eq .Role \\\\"assistant\\\\" }}}}model{{{{ else }}}}{{{{ .Role }}}}{{{{ end }}}}"
echo "{{{{ .Content }}}}<end_of_turn>"
echo "{{{{ end }}}}<start_of_turn>model\\""
echo "PARAMETER temperature 0.7"
echo "PARAMETER flash_attention on"
echo "PARAMETER stop \\"<end_of_turn>\\""
echo "PARAMETER stop \\"<start_of_turn>\\""'''
            if is_gemma4 else
            # ChatML template for Qwen models
            f'''echo "TEMPLATE \\"{{{{- if .System }}}}<|im_start|>system"
echo "{{{{ .System }}}}<|im_end|>"
echo "{{{{- end }}}}<|im_start|>user"
echo "{{{{ .Prompt }}}}<|im_end|>"
echo "<|im_start|>assistant"
echo "{{{{ .Response }}}}<|im_end|>\\""
echo "PARAMETER temperature 0.7"
echo "PARAMETER stop \\"<|im_end|>\\""'''
        ) + '''
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

    def export_local_notebook(
        self,
        model_name: str,
        corpus_path: str,
        epochs: int = 3,
    ) -> Path:
        """Generate a local VS Code-compatible training notebook.

        Works on MPS (Apple Silicon), CUDA, or CPU. Auto-detects hardware
        and uses Unsloth (CUDA) or standard PEFT (MPS/CPU) accordingly.

        Args:
            model_name: Key from MODEL_REGISTRY (e.g., "able-nano-9b").
            corpus_path: Path to training JSONL (ChatML format).
            epochs: Training epochs.

        Returns:
            Path to the generated .ipynb file.
        """
        config = MODEL_REGISTRY.get(model_name)
        if not config:
            raise ValueError(f"Unknown model: {model_name}. Available: {list(MODEL_REGISTRY)}")

        lora = _UNSLOTH_LORA_DEFAULTS.get(model_name, _UNSLOTH_LORA_DEFAULTS["able-nano-9b"])
        quants = _GGUF_QUANTS.get(model_name, ["q4_k_m"])
        is_gemma4 = model_name in _GEMMA4_MODELS

        cells = []

        # ── Title ────────────────────────────────────────────────
        cells.append(self._markdown_cell(
            f"# ABLE Distillation: Local Fine-Tune {config.base_model}\n\n"
            f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
            f"**Model**: {config.base_model} ({config.role})\n"
            f"**Corpus**: `{corpus_path}`\n"
            f"**Epochs**: {epochs}\n\n"
            f"Auto-detects hardware: **CUDA** (Unsloth 2x faster) | "
            f"**MPS** (PEFT LoRA) | **CPU** (testing only)\n\n"
            f"Run in VS Code Jupyter, JupyterLab, or any local notebook environment."
        ))

        # ── Config cell ──────────────────────────────────────────
        cells.append(self._code_cell(
            f'MODEL_NAME = "{model_name}"\n'
            f'BASE_MODEL = "{config.base_model}"\n'
            f'CORPUS_PATH = "{corpus_path}"\n'
            f"EPOCHS = {epochs}\n"
            f"LEARNING_RATE = {config.learning_rate}\n"
            f"MAX_SEQ_LENGTH = {lora['max_seq_length']}\n"
            f"LORA_R = {lora['r']}\n"
            f"LORA_ALPHA = {lora['lora_alpha']}\n"
            f"IS_GEMMA4 = {is_gemma4}\n"
            f"QUANT_METHODS = {quants}"
        ))

        # ── Environment detection ────────────────────────────────
        cells.append(self._code_cell(
            "import sys, platform, subprocess, torch\n"
            "from pathlib import Path\n\n"
            "DEVICE = 'cpu'\nUSE_UNSLOTH = False\nDTYPE = 'float32'\nMEMORY_GB = 0.0\n\n"
            "if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():\n"
            "    DEVICE = 'mps'\n"
            "    DTYPE = 'float16'\n"
            "    try:\n"
            "        r = subprocess.run(['sysctl', '-n', 'hw.memsize'],\n"
            "                           capture_output=True, text=True, timeout=5)\n"
            "        MEMORY_GB = round(int(r.stdout.strip()) / (1024**3), 1)\n"
            "    except Exception:\n"
            "        MEMORY_GB = 16.0\n"
            "elif torch.cuda.is_available():\n"
            "    DEVICE = 'cuda'\n"
            "    MEMORY_GB = round(torch.cuda.get_device_properties(0).total_mem / (1024**3), 1)\n"
            "    cap = torch.cuda.get_device_capability(0)\n"
            "    DTYPE = 'bfloat16' if cap[0] >= 8 else 'float16'\n"
            "    USE_UNSLOTH = True\n\n"
            "# Auto batch sizing\n"
            "if MEMORY_GB >= 64: BATCH_SIZE, GRAD_ACCUM = 8, 2\n"
            "elif MEMORY_GB >= 32: BATCH_SIZE, GRAD_ACCUM = 4, 4\n"
            "elif MEMORY_GB >= 16: BATCH_SIZE, GRAD_ACCUM = 2, 8\n"
            "else: BATCH_SIZE, GRAD_ACCUM = 1, 16\n\n"
            "print(f'Device: {DEVICE} | Memory: {MEMORY_GB}GB | '\n"
            "      f'Unsloth: {USE_UNSLOTH} | Batch: {BATCH_SIZE}x{GRAD_ACCUM}')"
        ))

        # ── Install deps ─────────────────────────────────────────
        cells.append(self._code_cell(
            "import subprocess, sys, os\n"
            "def pip_install(*pkgs):\n"
            "    subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-q'] + list(pkgs))\n\n"
            "if USE_UNSLOTH:\n"
            "    pip_install('unsloth>=2026.4.3')\n"
            "    pip_install('--no-deps', 'trl', 'peft', 'accelerate', 'bitsandbytes')\n"
            "else:\n"
            "    pip_install('torch', 'transformers', 'peft', 'trl', 'accelerate', 'datasets')\n\n"
            "os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'\n"
            "os.environ['TRANSFORMERS_NO_ADVISORY_WARNINGS'] = '1'\n"
            "print('Dependencies installed.')"
        ))

        # ── Load model ───────────────────────────────────────────
        target_modules_str = str(lora.get("target_modules", [
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ]))
        cells.append(self._code_cell(
            "import torch\n\n"
            "if USE_UNSLOTH:\n"
            "    from unsloth import FastLanguageModel\n"
            f"    model, tokenizer = FastLanguageModel.from_pretrained(\n"
            f"        model_name=BASE_MODEL, max_seq_length=MAX_SEQ_LENGTH,\n"
            f"        load_in_4bit=True, dtype=None)\n"
            f"    model = FastLanguageModel.get_peft_model(model, r=LORA_R,\n"
            f"        target_modules={target_modules_str},\n"
            f"        lora_alpha=LORA_ALPHA, lora_dropout=0,\n"
            f"        use_gradient_checkpointing='unsloth')\n"
            "else:\n"
            + (
                "    if IS_GEMMA4:\n"
                "        print('\\n' + '='*60)\n"
                "        print('ERROR: Gemma 4 requires Unsloth (CUDA) for safe training.')\n"
                "        print('KV-sharing corruption occurs with vanilla PEFT + gradient checkpointing.')\n"
                "        print('Options:')\n"
                "        print('  1. Use the Colab notebook (free T4 GPU, 24h runtime)')\n"
                "        print('  2. Use the MLX script (Apple Silicon native, no PEFT)')\n"
                "        print('  3. Use a CUDA machine with Unsloth installed')\n"
                "        print('='*60 + '\\n')\n"
                "        import sys; sys.exit(1)\n"
            if is_gemma4 else "") +
            "    from transformers import AutoModelForCausalLM, AutoTokenizer\n"
            "    from peft import LoraConfig, get_peft_model\n"
            "    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)\n"
            "    if tokenizer.pad_token is None: tokenizer.pad_token = tokenizer.eos_token\n"
            "    load_dtype = torch.float16 if DEVICE == 'mps' else torch.float32\n"
            "    model = AutoModelForCausalLM.from_pretrained(BASE_MODEL,\n"
            "        torch_dtype=load_dtype, attn_implementation='eager', low_cpu_mem_usage=True)\n"
            "    if DEVICE == 'mps': model = model.to('mps')\n"
            "    model.gradient_checkpointing_enable()\n"
            f"    lora_config = LoraConfig(r=LORA_R, lora_alpha=LORA_ALPHA,\n"
            f"        target_modules={target_modules_str},\n"
            f"        lora_dropout=0, task_type='CAUSAL_LM')\n"
            "    model = get_peft_model(model, lora_config)\n\n"
            "trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)\n"
            "total = sum(p.numel() for p in model.parameters())\n"
            "print(f'Model: {BASE_MODEL} — {trainable:,}/{total:,} trainable ({trainable/total*100:.2f}%)')"
        ))

        # ── Load corpus ──────────────────────────────────────────
        cells.append(self._code_cell(
            "from datasets import load_dataset\nfrom pathlib import Path\n\n"
            "corpus = Path(CORPUS_PATH)\n"
            "if not corpus.exists(): corpus = Path('../') / CORPUS_PATH\n"
            "if not corpus.exists(): raise FileNotFoundError(f'Corpus not found: {CORPUS_PATH}')\n\n"
            "def format_chatml(example):\n"
            "    messages = example.get('messages', example.get('conversations', []))\n"
            "    return {'text': tokenizer.apply_chat_template(\n"
            "        messages, tokenize=False, add_generation_prompt=False)}\n\n"
            "dataset = load_dataset('json', data_files=str(corpus), split='train')\n"
            "dataset = dataset.map(format_chatml)\n"
            "print(f'Loaded {len(dataset)} training examples')"
        ))

        # ── Train ────────────────────────────────────────────────
        gemma_note = (
            "if IS_GEMMA4:\n"
            "    print('NOTE: Gemma 4 training loss 10-15 is NORMAL. Do not stop early.')\n\n"
        ) if is_gemma4 else ""

        cells.append(self._code_cell(
            "from trl import SFTTrainer\nfrom transformers import TrainingArguments\n\n"
            + gemma_note +
            "training_kwargs = dict(\n"
            "    per_device_train_batch_size=BATCH_SIZE, gradient_accumulation_steps=GRAD_ACCUM,\n"
            "    num_train_epochs=EPOCHS, learning_rate=LEARNING_RATE,\n"
            "    logging_steps=10, save_strategy='steps', save_steps=100,\n"
            f"    output_dir=f'outputs/{model_name}', warmup_steps=5,\n"
            "    weight_decay=0.01, lr_scheduler_type='linear', seed=42, report_to='none')\n\n"
            "if USE_UNSLOTH:\n"
            "    from unsloth import is_bfloat16_supported\n"
            "    training_kwargs.update(fp16=not is_bfloat16_supported(),\n"
            "        bf16=is_bfloat16_supported(), optim='adamw_8bit')\n"
            "elif DEVICE == 'mps':\n"
            "    training_kwargs.update(fp16=False, bf16=False,\n"
            "        dataloader_pin_memory=False, optim='adamw_torch')\n"
            "else:\n"
            "    training_kwargs.update(fp16=False, bf16=False, no_cuda=True, optim='adamw_torch')\n\n"
            "trainer = SFTTrainer(model=model, tokenizer=tokenizer,\n"
            "    train_dataset=dataset, dataset_text_field='text',\n"
            "    max_seq_length=MAX_SEQ_LENGTH, dataset_num_proc=2,\n"
            "    args=TrainingArguments(**training_kwargs))\n\n"
            "trainer_stats = trainer.train()\n"
            "print(f'Training complete. Loss: {trainer_stats.training_loss:.4f}')"
        ))

        # ── Export GGUF ──────────────────────────────────────────
        cells.append(self._markdown_cell(
            "## Export to GGUF for Ollama"
        ))

        quant_list = ", ".join(f'"{q}"' for q in quants)
        cells.append(self._code_cell(
            "import os\n"
            f"os.makedirs(f'outputs/{model_name}-gguf', exist_ok=True)\n\n"
            "if USE_UNSLOTH:\n"
            f"    for quant in [{quant_list}]:\n"
            f"        print(f'Exporting {{quant}}...')\n"
            f"        model.save_pretrained_gguf(f'outputs/{model_name}-gguf',\n"
            f"            tokenizer, quantization_method=quant)\n"
            "else:\n"
            "    merged = model.merge_and_unload()\n"
            f"    merged.save_pretrained(f'outputs/{model_name}-merged')\n"
            f"    tokenizer.save_pretrained(f'outputs/{model_name}-merged')\n"
            f"    print(f'Merged model saved. Convert to GGUF with llama.cpp:')\n"
            f"    print(f'  python3 llama.cpp/convert_hf_to_gguf.py outputs/{model_name}-merged \\\\')\n"
            f"    print(f'    --outfile outputs/{model_name}-gguf/{model_name}-f16.gguf --outtype f16')"
        ))

        # ── Stats ────────────────────────────────────────────────
        cells.append(self._code_cell(
            "import json\nfrom datetime import datetime, timezone\n\n"
            "stats = {\n"
            f"    'model': '{model_name}', 'base': BASE_MODEL,\n"
            f"    'corpus_size': len(dataset), 'epochs': EPOCHS,\n"
            f"    'device': DEVICE, 'backend': 'unsloth' if USE_UNSLOTH else 'peft',\n"
            f"    'loss': trainer_stats.training_loss,\n"
            f"    'completed_at': datetime.now(timezone.utc).isoformat(),\n"
            "}\n"
            f"with open(f'outputs/{model_name}_training_stats.json', 'w') as f:\n"
            "    json.dump(stats, f, indent=2)\n"
            "print(json.dumps(stats, indent=2))"
        ))

        # ── Build notebook ───────────────────────────────────────
        notebook = {
            "nbformat": 4,
            "nbformat_minor": 0,
            "metadata": {
                "kernelspec": {
                    "name": "python3",
                    "display_name": "Python 3 (ipykernel)",
                },
                "language_info": {
                    "name": "python",
                },
            },
            "cells": cells,
        }

        self.output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"local_finetune_{model_name}.ipynb"
        filepath = self.output_dir / filename

        with open(filepath, "w") as f:
            json.dump(notebook, f, indent=2)

        logger.info("Generated local notebook: %s (%d cells)", filepath, len(cells))
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
