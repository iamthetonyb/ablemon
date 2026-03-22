"""
Distillation pipeline — corpus building, reasoning extraction, dataset versioning.

Converts harvested T4-quality conversations into versioned training datasets
for fine-tuning local Qwen 3.5 models on H100.
"""

from atlas.core.distillation.reasoning_extractor import ExtractionResult, ReasoningExtractor
from atlas.core.distillation.corpus_builder import CorpusBuildResult, CorpusBuilder
from atlas.core.distillation.dataset_versioner import DatasetVersion, DatasetVersioner

__all__ = [
    "ExtractionResult",
    "ReasoningExtractor",
    "CorpusBuildResult",
    "CorpusBuilder",
    "DatasetVersion",
    "DatasetVersioner",
]
