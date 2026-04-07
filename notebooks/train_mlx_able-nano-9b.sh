#!/usr/bin/env bash
# ABLE Distillation — Local MLX LoRA Fine-Tuning
# Generated: 2026-04-07 07:20 UTC
# Model: Qwen/Qwen3.5-9B (edge)
# Runtime: Apple Silicon MLX (unified memory)
#
# Requirements:
#   pip install "mlx-lm[train]"
#   The corpus must be ChatML JSONL with "messages" field.
#
# Memory: 9B 4-bit needs ~8-10GB for training. 36GB Mac = comfortable.
#         27B 4-bit needs ~20-24GB — only for 64GB+ Macs.

set -euo pipefail

MODEL="Qwen/Qwen3.5-9B-4bit"
CORPUS_DIR="$(dirname "/Users/abenton333/.able/distillation/corpus/default/v046/train.jsonl")"
ADAPTER_DIR="adapters/able-nano-9b"
FUSED_DIR="fused/able-nano-9b"
GGUF_DIR="gguf/able-nano-9b"

echo "══════════════════════════════════════════════════════════"
echo "  ABLE MLX LoRA Training: Qwen/Qwen3.5-9B"
echo "  Corpus: /Users/abenton333/.able/distillation/corpus/default/v046/train.jsonl"
echo "  Iterations: 600 | Batch: 1 | Layers: 8"
echo "══════════════════════════════════════════════════════════"

# ── Step 1: Install dependencies ──────────────────────────────
pip install -q "mlx-lm[train]" 2>/dev/null || true

# ── Step 2: Train LoRA adapter ────────────────────────────────
echo ""
echo "▸ Training LoRA adapter..."
python3 -m mlx_lm.lora \
    --model "$MODEL" \
    --train \
    --data "$CORPUS_DIR" \
    --adapter-path "$ADAPTER_DIR" \
    --batch-size 1 \
    --num-layers 8 \
    --lora-rank 16 \
    --iters 600 \
    --grad-checkpoint \
    --mask-prompt

echo "▸ Adapter saved to $ADAPTER_DIR"

# ── Step 3: Evaluate (optional) ──────────────────────────────
if [ -f "$CORPUS_DIR/valid.jsonl" ]; then
    echo ""
    echo "▸ Evaluating on validation set..."
    python3 -m mlx_lm.lora \
        --model "$MODEL" \
        --adapter-path "$ADAPTER_DIR" \
        --data "$CORPUS_DIR" \
        --test
fi

# ── Step 4: Fuse adapter into base model ─────────────────────
echo ""
echo "▸ Fusing adapter..."
python3 -m mlx_lm.fuse \
    --model "$MODEL" \
    --adapter-path "$ADAPTER_DIR" \
    --save-path "$FUSED_DIR"

echo "▸ Fused model at $FUSED_DIR"

# ── Step 5: Convert to GGUF for Ollama ───────────────────────
# Qwen is not in mlx-lm's native GGUF exporter, so we use llama.cpp.
echo ""
echo "▸ Converting to GGUF..."
mkdir -p "$GGUF_DIR"

if command -v python3 -c "import llama_cpp" &>/dev/null || [ -d "llama.cpp" ]; then
    # If llama.cpp is available locally
    python3 llama.cpp/convert_hf_to_gguf.py "$FUSED_DIR" \
        --outfile "$GGUF_DIR/able-nano-9b-f16.gguf" \
        --outtype f16
    echo "▸ GGUF exported: $GGUF_DIR/able-nano-9b-f16.gguf"
    echo ""
    echo "  To quantize further:"
    echo "    llama.cpp/llama-quantize $GGUF_DIR/able-nano-9b-f16.gguf $GGUF_DIR/able-nano-9b-q4_k_m.gguf q4_k_m"
else
    echo "▸ llama.cpp not found. Clone it for GGUF conversion:"
    echo "    git clone https://github.com/ggml-org/llama.cpp"
    echo "    pip install -r llama.cpp/requirements.txt"
    echo "    python3 llama.cpp/convert_hf_to_gguf.py $FUSED_DIR --outfile $GGUF_DIR/able-nano-9b-f16.gguf --outtype f16"
fi

# ── Step 6: Register in Ollama ───────────────────────────────
echo ""
echo "▸ To deploy in Ollama:"
echo "    cat > Modelfile <<MODELFILE"
echo "FROM ./$GGUF_DIR/able-nano-9b-q4_k_m.gguf"
echo "TEMPLATE \"{{- if .System }}<|im_start|>system"
echo "{{ .System }}<|im_end|>"
echo "{{- end }}<|im_start|>user"
echo "{{ .Prompt }}<|im_end|>"
echo "<|im_start|>assistant"
echo "{{ .Response }}<|im_end|>\""
echo "PARAMETER temperature 0.7"
echo "PARAMETER stop \"<|im_end|>\""
echo "SYSTEM You are ABLE, an autonomous AI agent."
echo "MODELFILE"
echo ""
echo "    ollama create able-nano-9b-mlx -f Modelfile"
echo "    ollama run able-nano-9b-mlx"

echo ""
echo "══════════════════════════════════════════════════════════"
echo "  Training complete. Adapter: $ADAPTER_DIR"
echo "  Fused model: $FUSED_DIR"
echo "══════════════════════════════════════════════════════════"
