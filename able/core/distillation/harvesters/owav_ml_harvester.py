"""
0wav ML Training Data Harvester — imports labeled behavioral profiles
from the ai-0wav-hub project into ABLE's distillation pipeline.

This harvester reads 0wav's existing training data:
  - manifest.json (228 labeled samples with acoustic features + behavioral profiles)
  - quality_weights.json (per-sample quality scores from NVIDIA Granary-inspired filtering)
  - features/ (2048-dim Qwen3-ASR encoder feature .npy files)

Each labeled sample becomes a distillation pair:
  - Prompt: transcript + acoustic features (what the model sees)
  - Gold response: full behavioral profile (what the model should produce)
  - Teacher: gemini+voxtral ensemble (the LLM teacher that generated labels)

Also harvests Claude Code sessions from the 0wav project for text-based
audio ML reasoning (coding, architecture, pipeline design).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from able.core.distillation.harvesters.base import (
    BaseHarvester,
    HarvestedConversation,
)

logger = logging.getLogger(__name__)

# Default 0wav project path
DEFAULT_0WAV_PROJECT = "/Users/abenton333/Desktop/ai-0wav-hub"

# 0wav-specific domains for classification
_0WAV_DOMAINS = {
    "behavioral-profiling",
    "audio-processing",
    "asr-pipeline",
    "ml-training",
    "hdf5-containers",
}


class OwavMLHarvester(BaseHarvester):
    """Harvests 0wav's labeled ML training data into ABLE distillation pairs.

    Two data sources:
    1. Labeled behavioral profiles (manifest.json) — audio ML ground truth
    2. Claude Code sessions from the 0wav project — text reasoning about audio ML
    """

    source_name = "0wav_ml"

    def __init__(
        self,
        project_path: str = DEFAULT_0WAV_PROJECT,
        min_quality: float = 0.3,
    ):
        self.project_path = Path(project_path)
        self.min_quality = min_quality
        self._manifest: list[dict] | None = None
        self._quality_weights: dict | None = None

    def harvest(
        self,
        source_path: str | Path | None = None,
        since: datetime | None = None,
    ) -> list[HarvestedConversation]:
        """Harvest 0wav ML training data as conversations."""
        root = Path(source_path) if source_path else self.project_path
        training_dir = root / "training_data"

        if not training_dir.exists():
            logger.warning("[0wav] Training data not found at %s", training_dir)
            return []

        conversations = []

        # 1. Harvest labeled behavioral profiles from manifest
        labeled = self._harvest_labeled_profiles(training_dir)
        conversations.extend(labeled)

        # 2. Harvest Claude Code sessions from 0wav project
        cc_sessions = self._harvest_claude_sessions(root)
        conversations.extend(cc_sessions)

        logger.info(
            "[0wav] Harvested %d labeled profiles + %d Claude sessions = %d total",
            len(labeled),
            len(cc_sessions),
            len(conversations),
        )
        return conversations

    # ── Labeled profile harvesting ────────────────────────────────────

    def _harvest_labeled_profiles(
        self, training_dir: Path
    ) -> list[HarvestedConversation]:
        """Convert each labeled sample → distillation conversation pair."""
        manifest_path = training_dir / "manifest.json"
        weights_path = training_dir / "quality_weights.json"

        if not manifest_path.exists():
            logger.debug("[0wav] No manifest.json found")
            return []

        manifest = self._load_manifest(manifest_path)
        weights = self._load_quality_weights(weights_path)

        conversations = []
        for sample in manifest.get("samples", []):
            stem = sample.get("stem", "")
            if not stem:
                continue

            # Quality filter
            quality = weights.get(stem, {}).get("quality", 0.5)
            if quality < self.min_quality:
                continue

            # Build the prompt (what the model receives)
            prompt = self._build_prompt(sample)
            if not prompt:
                continue

            # Build the gold response (what the model should produce)
            response = self._build_response(sample)
            if not response:
                continue

            # Determine domain
            domain = self._classify_0wav_domain(sample)

            # Content hash scoped to 0wav tenant
            content_for_hash = f"0wav:{prompt[:500]}:{response[:500]}"
            content_hash = hashlib.sha256(content_for_hash.encode()).hexdigest()

            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are an expert behavioral profiling AI. Given audio "
                        "features and a transcript, produce a comprehensive "
                        "behavioral fingerprint including NLP meta-programs, "
                        "communication style, and macro-classifications."
                    ),
                },
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": response},
            ]

            conv = HarvestedConversation(
                id=f"0wav-ml-{stem}",
                source="0wav_ml",
                messages=messages,
                model="gemini+voxtral-ensemble",
                timestamp=datetime.utcnow(),
                domain=domain,
                metadata={
                    "stem": stem,
                    "quality_score": quality,
                    "quality_weight": weights.get(stem, {}).get("weight", 0.5),
                    "teacher_model": "gemini+voxtral-ensemble",
                    "data_type": "behavioral_profile",
                    "features_dim": 2048,
                    "fingerprint_dim": 32,
                    "source_project": "ai-0wav-hub",
                },
                content_hash=content_hash,
            )
            conversations.append(conv)

        return conversations

    def _build_prompt(self, sample: dict) -> str:
        """Build the input prompt from audio features and transcript."""
        parts = []

        # Transcript
        transcript = sample.get("transcript", "")
        if transcript:
            parts.append(f"## Transcript\n{transcript}")

        # Acoustic features
        acoustic = []
        for key in (
            "f0_mean", "f0_std", "f0_min", "f0_max",
            "energy_mean", "energy_std",
            "jitter_local", "jitter_rap",
            "shimmer_local", "shimmer_apq3",
            "hnr_mean", "speaking_rate_sps", "articulation_rate_sps",
            "pause_count", "pause_total_s", "duration_s",
        ):
            val = sample.get(key)
            if val is not None:
                acoustic.append(f"  {key}: {val}")

        if acoustic:
            parts.append("## Acoustic Features\n" + "\n".join(acoustic))

        # Prosody-derived scores
        prosody = []
        for key in (
            "tone_label", "tone_level", "tone_confidence",
            "cognitive_load_index", "engagement_score",
        ):
            val = sample.get(key)
            if val is not None:
                prosody.append(f"  {key}: {val}")

        if prosody:
            parts.append("## Prosody Analysis\n" + "\n".join(prosody))

        if not parts:
            return ""

        return (
            "Analyze the following audio sample and produce a comprehensive "
            "behavioral profile:\n\n" + "\n\n".join(parts)
        )

    def _build_response(self, sample: dict) -> str:
        """Build the gold response from behavioral labels."""
        parts = []

        # Communication style
        comm_style = sample.get("communication_style")
        if comm_style:
            lines = [f"  {k}: {v:.3f}" for k, v in comm_style.items()]
            parts.append("## Communication Style\n" + "\n".join(lines))

        # Meta-programs
        meta = sample.get("meta_programs")
        if meta:
            mp_lines = []
            for program, poles in meta.items():
                if isinstance(poles, dict):
                    pole_str = ", ".join(f"{k}: {v:.3f}" for k, v in poles.items())
                    mp_lines.append(f"  {program}: {pole_str}")
            parts.append("## NLP Meta-Programs\n" + "\n".join(mp_lines))

        # Macro classifications
        macro = sample.get("macro_classifications")
        if macro:
            for cls_name, cls_data in macro.items():
                if isinstance(cls_data, dict):
                    primary = cls_data.get("primary", "")
                    reasoning = cls_data.get("reasoning", "")
                    scores = {
                        k: v for k, v in cls_data.items()
                        if k.startswith("scores_") or k in ("primary_level",)
                    }
                    mc_lines = [f"  Primary: {primary}"]
                    if reasoning:
                        mc_lines.append(f"  Reasoning: {reasoning}")
                    for sk, sv in scores.items():
                        mc_lines.append(f"  {sk}: {sv}")
                    parts.append(
                        f"## {cls_name.replace('_', ' ').title()}\n"
                        + "\n".join(mc_lines)
                    )

        if not parts:
            return ""

        return (
            "<think>\nAnalyzing acoustic features, prosody, and linguistic "
            "patterns to derive behavioral profile dimensions.\n</think>\n\n"
            + "\n\n".join(parts)
        )

    def _classify_0wav_domain(self, sample: dict) -> str:
        """Classify the sample into a 0wav domain."""
        if sample.get("meta_programs") or sample.get("macro_classifications"):
            return "behavioral-profiling"
        if sample.get("communication_style"):
            return "behavioral-profiling"
        return "audio-processing"

    # ── Claude Code session harvesting ────────────────────────────────

    def _harvest_claude_sessions(
        self, project_root: Path
    ) -> list[HarvestedConversation]:
        """Harvest Claude Code sessions from the 0wav project directory."""
        try:
            from able.core.distillation.harvesters.claude_code_harvester import (
                ClaudeCodeHarvester,
            )
        except ImportError:
            logger.debug("[0wav] Claude Code harvester unavailable")
            return []

        # 0wav-specific Claude session dirs
        sessions_dirs = [
            Path.home() / ".claude" / "projects" / "-Users-abenton333-Desktop-ai-0wav-hub",
        ]

        conversations = []
        for sessions_dir in sessions_dirs:
            if not sessions_dir.exists():
                continue
            try:
                harvester = ClaudeCodeHarvester()
                convos = harvester.harvest(source_path=str(sessions_dir))
                # Re-tag for 0wav tenant
                for c in convos:
                    c.source = "0wav_claude_code"
                    c.metadata["tenant_id"] = "0wav"
                    c.metadata["source_project"] = "ai-0wav-hub"
                    # Override domain detection for audio ML
                    if not c.domain or c.domain == "coding":
                        c.domain = "ml-training"
                conversations.extend(convos)
            except Exception as e:
                logger.warning("[0wav] Claude session harvest failed: %s", e)

        return conversations

    # ── Data loading ──────────────────────────────────────────────────

    def _load_manifest(self, path: Path) -> dict:
        """Load and cache the manifest."""
        if self._manifest is None:
            with open(path) as f:
                data = json.load(f)
            self._manifest = data if isinstance(data, dict) else {"samples": []}
        return self._manifest

    def _load_quality_weights(self, path: Path) -> dict:
        """Load and cache quality weights."""
        if self._quality_weights is None:
            if path.exists():
                with open(path) as f:
                    self._quality_weights = json.load(f)
            else:
                self._quality_weights = {}
        return self._quality_weights


class OwavPipelineStats:
    """Read-only view of 0wav's ML pipeline status for ABLE monitoring."""

    def __init__(self, project_path: str = DEFAULT_0WAV_PROJECT):
        self.project_path = Path(project_path)

    def get_stats(self) -> dict:
        """Get current 0wav pipeline stats for Phoenix/dashboard."""
        training_dir = self.project_path / "training_data"
        stats: dict = {
            "project": str(self.project_path),
            "exists": self.project_path.exists(),
            "training_data": {},
            "models": {},
            "features": {},
        }

        if not training_dir.exists():
            return stats

        # Manifest stats
        manifest_path = training_dir / "manifest.json"
        if manifest_path.exists():
            with open(manifest_path) as f:
                manifest = json.load(f)
            samples = manifest.get("samples", [])
            stats["training_data"]["total_samples"] = len(samples)
            stats["training_data"]["version"] = manifest.get("version", "unknown")

            # Count samples with full behavioral profiles
            with_profiles = sum(
                1 for s in samples
                if s.get("meta_programs") and s.get("macro_classifications")
            )
            stats["training_data"]["with_full_profiles"] = with_profiles

            # Count transcripts
            with_transcripts = sum(1 for s in samples if s.get("transcript"))
            stats["training_data"]["with_transcripts"] = with_transcripts

        # Quality weights
        weights_path = training_dir / "quality_weights.json"
        if weights_path.exists():
            with open(weights_path) as f:
                weights = json.load(f)
            qualities = [v.get("quality", 0) for v in weights.values()]
            if qualities:
                stats["training_data"]["avg_quality"] = round(
                    sum(qualities) / len(qualities), 3
                )
                stats["training_data"]["min_quality"] = round(min(qualities), 3)
                stats["training_data"]["above_threshold"] = sum(
                    1 for q in qualities if q >= 0.3
                )

        # Features
        features_dir = training_dir / "features"
        if features_dir.exists():
            npy_files = list(features_dir.glob("*.npy"))
            stats["features"]["count"] = len(npy_files)
            stats["features"]["dim"] = 2048  # Qwen3-ASR encoder

        # Models
        models_dir = training_dir / "models"
        if models_dir.exists():
            model_files = list(models_dir.glob("projector_*.pt"))
            stats["models"]["checkpoints"] = len(model_files)
            best = models_dir / "projector_best.pt"
            if best.exists():
                stats["models"]["best_size_mb"] = round(
                    best.stat().st_size / (1024 * 1024), 1
                )
                stats["models"]["best_modified"] = datetime.fromtimestamp(
                    best.stat().st_mtime
                ).isoformat()

        return stats
