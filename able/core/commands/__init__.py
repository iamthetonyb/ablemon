"""
Slash Commands System

Register and execute slash commands with auto-discovery.
"""

from .slash_commands import (
    SlashCommandRegistry,
    SlashCommand,
    CommandCategory,
    CommandArgument,
    get_command_registry,
    command,
)

__all__ = [
    "SlashCommandRegistry",
    "SlashCommand",
    "CommandCategory",
    "CommandArgument",
    "get_command_registry",
    "command",
]
