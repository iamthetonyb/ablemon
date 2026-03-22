"""
ATLAS Distillation Pipeline — Build high-quality training corpora from interaction logs.

Components:
- CorpusBuilder: Filter and assemble training data from interaction logs
- ReasoningExtractor: Normalize chain-of-thought reasoning formats
- DatasetVersioner: Git-based versioning for training datasets
"""

__all__ = [
    "CorpusBuilder",
    "ReasoningExtractor",
    "DatasetVersioner",
]


def __getattr__(name: str):
    if name == "CorpusBuilder":
        from .corpus_builder import CorpusBuilder
        return CorpusBuilder
    elif name == "ReasoningExtractor":
        from .reasoning_extractor import ReasoningExtractor
        return ReasoningExtractor
    elif name == "DatasetVersioner":
        from .dataset_versioner import DatasetVersioner
        return DatasetVersioner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
