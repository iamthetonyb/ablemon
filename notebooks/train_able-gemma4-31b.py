#!/usr/bin/env python3
"""ABLE distillation training script — Unsloth + google/gemma-4-31b-it

Generated: 2026-04-11 21:45 UTC
Model: google/gemma-4-31b-it (server)
Corpus: data/corpus/default/v048/train.jsonl

Run via VS Code connected to Colab runtime, or directly with a GPU:
    python train_able-gemma4-31b.py
"""

from unsloth import FastLanguageModel, is_bfloat16_supported
from datasets import load_dataset
from trl import SFTTrainer
from transformers import TrainingArguments
import json
from datetime import datetime, timezone

# ── Config ─────────────────────────────────────────────────────
MODEL_NAME = "google/gemma-4-31b-it"
_CORPUS_RAW = "data/corpus/default/v048/train.jsonl"
MAX_SEQ_LENGTH = 8192
LORA_R = 8
LORA_ALPHA = 8
EPOCHS = 3
BATCH_SIZE = 1
GRAD_ACCUM = 8
LR = 0.0001
QUANTS = ['q4_k_m', 'q5_k_m', 'q8_0']

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
    raise FileNotFoundError(f"Corpus not found: {_CORPUS_RAW} — run harvest first or set path manually")
CORPUS_PATH = str(_corpus)
print(f"Corpus: {CORPUS_PATH}")

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
    messages = example.get("messages", example.get("conversations", []))
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
        output_dir=f"outputs/able-gemma4-31b",
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
        f"outputs/able-gemma4-31b-gguf",
        tokenizer,
        quantization_method=quant,
    )

# ── Save stats ─────────────────────────────────────────────────
training_stats = {
    "model": "able-gemma4-31b",
    "base": MODEL_NAME,
    "corpus_size": len(dataset),
    "epochs": EPOCHS,
    "loss": stats.training_loss,
    "quants": QUANTS,
    "completed_at": datetime.now(timezone.utc).isoformat(),
}
with open(f"outputs/able-gemma4-31b_training_stats.json", "w") as f:
    json.dump(training_stats, f, indent=2)

print(json.dumps(training_stats, indent=2))
print(f"\nGGUF files in outputs/able-gemma4-31b-gguf/")
print(f"Create Ollama model: ollama create able-gemma4-31b -f Modelfile")
