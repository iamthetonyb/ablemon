"""
Channel Adapters

Implementations for different messaging platforms.
"""

from .base import ChannelAdapter
from .telegram import TelegramAdapter
from .discord import DiscordAdapter
from .slack import SlackAdapter

__all__ = [
    'ChannelAdapter',
    'TelegramAdapter',
    'DiscordAdapter',
    'SlackAdapter',
]
