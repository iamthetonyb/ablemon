"""
Conversation harvesters for the ABLE distillation pipeline.

Each harvester extracts training-quality conversations from a different
source (Claude Code sessions, ABLE interaction log, manual inbox, CLI
sessions, or any OpenCLI-compatible platform) and normalises them into
HarvestedConversation objects for the TrainingFormatter.
"""

from able.core.distillation.harvesters.base import (
    BaseHarvester,
    HarvestedConversation,
)
from able.core.distillation.harvesters.cli_session_harvester import (
    CLISessionHarvester,
)

__all__ = ["BaseHarvester", "CLISessionHarvester", "HarvestedConversation"]
