"""
Conversation harvesters for the ATLAS distillation pipeline.

Each harvester extracts training-quality conversations from a different
source (Claude Code sessions, ATLAS interaction log, manual inbox, CLI
sessions, or any OpenCLI-compatible platform) and normalises them into
HarvestedConversation objects for the TrainingFormatter.
"""

from atlas.core.distillation.harvesters.base import (
    BaseHarvester,
    HarvestedConversation,
)
from atlas.core.distillation.harvesters.cli_session_harvester import (
    CLISessionHarvester,
)

__all__ = ["BaseHarvester", "CLISessionHarvester", "HarvestedConversation"]
