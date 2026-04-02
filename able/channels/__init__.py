"""
ABLE v2 Multi-Channel Gateway

PicoClaw-inspired channel-agnostic message routing.
Supports: Telegram, Discord, Slack, CLI/API
"""

from .unified_gateway import UnifiedGateway, ChannelConfig
from .normalized_message import NormalizedMessage, MessageType
from .adapters import ChannelAdapter

__all__ = [
    'UnifiedGateway',
    'ChannelConfig',
    'NormalizedMessage',
    'MessageType',
    'ChannelAdapter',
]
