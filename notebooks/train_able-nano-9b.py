#!/usr/bin/env python3
"""ABLE distillation training script — Unsloth + Qwen/Qwen3.5-9B

Generated: 2026-04-07 07:20 UTC
Model: Qwen/Qwen3.5-9B (edge)
Corpus: ~/.able/distillation/corpus/default/latest/train.jsonl

Run via VS Code connected to Colab runtime, or directly with a GPU:
    python train_able-nano-9b.py
"""

from unsloth import FastLanguageModel, is_bfloat16_supported
from datasets import load_dataset
from trl import SFTTrainer
from transformers import TrainingArguments
import json
from datetime import datetime, timezone

# ── Config ─────────────────────────────────────────────────────
MODEL_NAME = "Qwen/Qwen3.5-9B"
import os as _os
CORPUS_PATH = _os.path.expanduser("~/.able/distillation/corpus/default/latest/train.jsonl")
MAX_SEQ_LENGTH = 2048
LORA_R = 16
LORA_ALPHA = 16
EPOCHS = 3
BATCH_SIZE = 1
GRAD_ACCUM = 8
LR = 0.0002
QUANTS = ['q4_k_m', 'iq2_m', 'q8_0']

# ── Load model ─────────────────────────────────────────────────
print(f"Loading {MODEL_NAME}...")
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
print(f"Trainable params: {model.get_nb_trainable_parameters()}")

# ── Load corpus ────────────────────────────────────────────────
def format_chatml(example):
    messages = example.get("messages", [])
    return {"text": tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=False
    )}

dataset = load_dataset("json", data_files=CORPUS_PATH, split="train")
dataset = dataset.map(format_chatml)
print(f"Loaded {len(dataset)} training examples")

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
        output_dir=f"outputs/able-nano-9b",
        optim="adamw_8bit",
        warmup_steps=5,
        weight_decay=0.01,
        lr_scheduler_type="linear",
        seed=42,
    ),
)

stats = trainer.train()
print(f"Training complete: loss={stats.training_loss:.4f}")

# ── Export GGUF ────────────────────────────────────────────────
for quant in QUANTS:
    print(f"Exporting {quant}...")
    model.save_pretrained_gguf(
        f"outputs/able-nano-9b-gguf",
        tokenizer,
        quantization_method=quant,
    )

# ── Save stats ─────────────────────────────────────────────────
training_stats = {
    "model": "able-nano-9b",
    "base": MODEL_NAME,
    "corpus_size": len(dataset),
    "epochs": EPOCHS,
    "loss": stats.training_loss,
    "quants": QUANTS,
    "completed_at": datetime.now(timezone.utc).isoformat(),
}
with open(f"outputs/able-nano-9b_training_stats.json", "w") as f:
    json.dump(training_stats, f, indent=2)

print(json.dumps(training_stats, indent=2))
print(f"\nGGUF files in outputs/able-nano-9b-gguf/")
print(f"Create Ollama model: ollama create able-nano-9b -f Modelfile")
