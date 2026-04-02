"""Generates Axolotl YAML configs for QLoRA fine-tuning.

Produces a fully-resolved YAML file that Axolotl can consume directly.
Key design choices:
- train_on_inputs: False  (mask instructions, train on responses only)
- chat_template: chatml   (Qwen native format)
- QLoRA 4-bit via bitsandbytes
- Per-tenant output directories for multi-tenant training
"""

from __future__ import annotations

import os
from typing import Any

import yaml

from able.core.distillation.training.model_configs import (
    StudentModelConfig,
    resolve_runtime_profile,
)


class AxolotlConfigGenerator:
    """Generates Axolotl YAML configs for QLoRA fine-tuning."""

    def generate(
        self,
        model_config: StudentModelConfig,
        corpus_path: str,
        output_path: str,
        epochs: int = 3,
        tenant_id: str = "default",
        gpu_class: str | None = None,
        runtime: str | None = None,
        checkpoint_dir: str | None = None,
        resume: bool = False,
    ) -> str:
        """Generate an Axolotl YAML config and write it to disk.

        Args:
            model_config: Student model configuration.
            corpus_path: Path to the training corpus (JSONL or directory).
            output_path: Directory for training outputs (adapters, logs).
            epochs: Number of training epochs.
            tenant_id: Tenant identifier for multi-tenant isolation.

        Returns:
            Absolute path to the generated YAML config file.
        """
        tenant_output = os.path.join(output_path, tenant_id, model_config.name)

        config = self._build_config(
            model_config=model_config,
            corpus_path=corpus_path,
            output_dir=tenant_output,
            epochs=epochs,
            gpu_class=gpu_class,
            runtime=runtime,
            checkpoint_dir=checkpoint_dir,
            resume=resume,
        )

        config_dir = os.path.join(tenant_output, "configs")
        os.makedirs(config_dir, exist_ok=True)
        config_path = os.path.join(config_dir, "axolotl.yaml")

        with open(config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        return config_path

    def generate_dict(
        self,
        model_config: StudentModelConfig,
        corpus_path: str,
        output_path: str,
        epochs: int = 3,
        tenant_id: str = "default",
        gpu_class: str | None = None,
        runtime: str | None = None,
        checkpoint_dir: str | None = None,
        resume: bool = False,
    ) -> dict[str, Any]:
        """Generate the config as a dict without writing to disk.

        Same arguments as generate(). Useful for inspection and testing.
        """
        tenant_output = os.path.join(output_path, tenant_id, model_config.name)
        return self._build_config(
            model_config=model_config,
            corpus_path=corpus_path,
            output_dir=tenant_output,
            epochs=epochs,
            gpu_class=gpu_class,
            runtime=runtime,
            checkpoint_dir=checkpoint_dir,
            resume=resume,
        )

    def _build_config(
        self,
        model_config: StudentModelConfig,
        corpus_path: str,
        output_dir: str,
        epochs: int,
        gpu_class: str | None,
        runtime: str | None,
        checkpoint_dir: str | None,
        resume: bool,
    ) -> dict[str, Any]:
        """Build the Axolotl config dictionary."""
        runtime_profile = resolve_runtime_profile(
            model_config,
            gpu_class=gpu_class,
            runtime=runtime,
        )
        checkpoint_path = checkpoint_dir or os.path.join(output_dir, "checkpoints")
        os.makedirs(checkpoint_path, exist_ok=True)

        return {
            # Model
            "base_model": model_config.base_model,
            "model_type": "AutoModelForCausalLM",
            "load_in_4bit": True,
            # LoRA / QLoRA
            "adapter": "qlora",
            "lora_r": model_config.lora_r,
            "lora_alpha": model_config.lora_alpha,
            "lora_target_linear": True,
            # Data
            "datasets": [{"path": corpus_path, "type": "chatml"}],
            "train_on_inputs": False,
            "chat_template": "chatml",
            "sequence_len": runtime_profile["sequence_len"],
            # Training
            "micro_batch_size": runtime_profile["micro_batch_size"],
            "gradient_accumulation_steps": runtime_profile["gradient_accumulation"],
            "learning_rate": model_config.learning_rate,
            "num_epochs": epochs,
            "warmup_steps": 10,
            "weight_decay": 0.01,
            # Precision
            "bf16": runtime_profile["bf16"],
            "fp16": runtime_profile["fp16"],
            "tf32": True,
            # Checkpointing
            "gradient_checkpointing": runtime_profile["gradient_checkpointing"],
            "save_strategy": runtime_profile.get("save_strategy", "epoch"),
            "save_steps": runtime_profile.get("save_steps", 250),
            "output_dir": output_dir,
            "save_total_limit": 3,
            "checkpoint_dir": checkpoint_path,
            "resume_from_checkpoint": checkpoint_path if resume else None,
            "able_runtime": {
                "gpu_class": runtime_profile["gpu_class"],
                "runtime": runtime_profile["runtime"],
                "checkpointing": model_config.checkpointing,
                "resume_first": model_config.resume_first,
            },
        }
