"""LoRA merge + GGUF quantization utilities.

Generates shell commands (not executes them) so the operator can review
before running on expensive hardware. Also produces Ollama Modelfiles
for deploying the quantized models.

27B targets: UD-Q4_K_XL (17.6GB PRIMARY), Q5_K_M (19.6GB), Q8_0 (28.6GB)
9B targets:  UD-IQ2_M (3.65GB EDGE), UD-Q4_K_XL (5.97GB BALANCED), Q5_K_M (6.58GB)

ALWAYS prefer Unsloth Dynamic (UD-) variants.
"""

from __future__ import annotations

import os
import textwrap


# GGUF size estimates in GB, used for reporting only.
_QUANT_SIZES: dict[str, dict[str, float]] = {
    "atlas-student-27b": {
        "UD-Q4_K_XL": 17.6,
        "Q5_K_M": 19.6,
        "Q8_0": 28.6,
    },
    "atlas-nano-9b": {
        "UD-IQ2_M": 3.65,
        "UD-Q4_K_XL": 5.97,
        "Q5_K_M": 6.58,
    },
}

class GGUFQuantizer:
    """Merge LoRA + quantize to GGUF. Runs on CPU."""

    def generate_merge_command(
        self,
        adapter_path: str,
        base_model: str,
        output_path: str,
    ) -> str:
        """Generate a shell command to merge a LoRA adapter with the base model.

        Uses the ``unsloth`` CLI for merging (preferred) with a
        ``transformers`` fallback note.
        """
        return (
            f"python -m unsloth.merge "
            f"--base_model {base_model} "
            f"--adapter {adapter_path} "
            f"--output_dir {output_path} "
            f"--push_to_hub false"
        )

    def generate_quantize_command(
        self,
        model_path: str,
        quant_type: str,
        output_path: str,
    ) -> str:
        """Generate a llama.cpp quantize command.

        For UD- (Unsloth Dynamic) quant types the recommended path is
        ``unsloth quantize``. For standard types use ``llama-quantize``.
        """
        output_file = os.path.join(
            output_path,
            f"{os.path.basename(model_path)}-{quant_type}.gguf",
        )

        if quant_type.startswith("UD-"):
            return (
                f"python -m unsloth.quantize "
                f"--model {model_path} "
                f"--quant_type {quant_type} "
                f"--output {output_file}"
            )

        # Standard llama.cpp quantization
        return f"llama-quantize {model_path} {output_file} {quant_type}"

    def generate_ollama_modelfile(
        self,
        gguf_path: str,
        model_name: str,
    ) -> str:
        """Generate an Ollama Modelfile for a quantized GGUF.

        Uses chatml template (Qwen native).
        """
        return textwrap.dedent(f"""\
            # {model_name} — ATLAS fine-tuned model
            FROM {gguf_path}

            PARAMETER temperature 0.7
            PARAMETER num_ctx 131072
            PARAMETER stop <|im_end|>
            PARAMETER stop <|endoftext|>

            TEMPLATE \"\"\"{{{{- if .System }}}}<|im_start|>system
            {{{{ .System }}}}<|im_end|>
            {{{{ end }}}}{{{{- range .Messages }}}}<|im_start|>{{{{ .Role }}}}
            {{{{ .Content }}}}<|im_end|>
            {{{{ end }}}}<|im_start|>assistant
            \"\"\"
        """)

    def estimated_size_gb(self, model_name: str, quant_type: str) -> float | None:
        """Return estimated GGUF size in GB, or None if unknown."""
        return _QUANT_SIZES.get(model_name, {}).get(quant_type)
