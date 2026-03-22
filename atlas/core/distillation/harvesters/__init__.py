"""
Conversation harvesters for the ATLAS distillation pipeline.

Each harvester extracts training-quality conversations from a different
source (Claude Code sessions, ATLAS interaction log, manual inbox, or
any OpenCLI-compatible platform) and normalises them into
HarvestedConversation objects for the TrainingFormatter.
"""

from atlas.core.distillation.harvesters.base import (
    BaseHarvester,
    HarvestedConversation,
)

__all__ = ["BaseHarvester", "HarvestedConversation"]
