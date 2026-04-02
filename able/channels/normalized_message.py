"""
Normalized Message Format

Channel-agnostic message representation.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any


class MessageType(str, Enum):
    TEXT = "text"
    COMMAND = "command"
    CALLBACK = "callback"
    FILE = "file"
    IMAGE = "image"
    AUDIO = "audio"
    SYSTEM = "system"


@dataclass
class MessageAttachment:
    """File or media attachment"""
    type: str  # image, file, audio, video
    url: Optional[str] = None
    data: Optional[bytes] = None
    filename: Optional[str] = None
    mime_type: Optional[str] = None
    size_bytes: int = 0


@dataclass
class MessageSender:
    """Sender information"""
    id: str
    username: Optional[str] = None
    display_name: Optional[str] = None
    is_bot: bool = False
    is_owner: bool = False
    trust_tier: str = "L1_OBSERVE"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedMessage:
    """
    Channel-agnostic message format.

    Converts from Telegram, Discord, Slack, etc. to common format.
    """
    # Identification
    id: str
    channel: str  # telegram, discord, slack, cli
    conversation_id: str  # chat_id, channel_id, thread_id

    # Content
    type: MessageType
    text: str
    command: Optional[str] = None  # /start, /help, etc.
    command_args: Optional[str] = None

    # Sender
    sender: MessageSender = field(default_factory=lambda: MessageSender(id="unknown"))

    # Context
    reply_to_id: Optional[str] = None
    thread_id: Optional[str] = None

    # Attachments
    attachments: List[MessageAttachment] = field(default_factory=list)

    # Metadata
    timestamp: datetime = field(default_factory=datetime.utcnow)
    raw_message: Optional[Any] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_command(self) -> bool:
        """Check if message is a command"""
        return self.type == MessageType.COMMAND or (
            self.text and self.text.startswith('/')
        )

    def get_command(self) -> Optional[str]:
        """Extract command name (without /)"""
        if self.command:
            return self.command.lstrip('/')
        if self.text and self.text.startswith('/'):
            parts = self.text.split(None, 1)
            return parts[0].lstrip('/').split('@')[0]
        return None

    def get_command_args(self) -> str:
        """Get command arguments"""
        if self.command_args:
            return self.command_args
        if self.text and ' ' in self.text:
            return self.text.split(None, 1)[1]
        return ""

    def has_attachments(self) -> bool:
        """Check if message has attachments"""
        return len(self.attachments) > 0

    @classmethod
    def from_telegram(cls, message) -> 'NormalizedMessage':
        """Create from telegram.Message"""
        text = message.text or message.caption or ""

        # Detect command
        command = None
        command_args = None
        msg_type = MessageType.TEXT

        if text.startswith('/'):
            msg_type = MessageType.COMMAND
            parts = text.split(None, 1)
            command = parts[0]
            command_args = parts[1] if len(parts) > 1 else None

        # Build sender
        user = message.from_user
        sender = MessageSender(
            id=str(user.id) if user else "unknown",
            username=user.username if user else None,
            display_name=user.first_name if user else None,
            is_bot=user.is_bot if user else False,
        )

        # Collect attachments
        attachments = []
        if message.photo:
            largest = max(message.photo, key=lambda p: p.file_size or 0)
            attachments.append(MessageAttachment(
                type="image",
                size_bytes=largest.file_size or 0,
            ))
        if message.document:
            attachments.append(MessageAttachment(
                type="file",
                filename=message.document.file_name,
                mime_type=message.document.mime_type,
                size_bytes=message.document.file_size or 0,
            ))

        return cls(
            id=str(message.message_id),
            channel="telegram",
            conversation_id=str(message.chat.id),
            type=msg_type,
            text=text,
            command=command,
            command_args=command_args,
            sender=sender,
            reply_to_id=str(message.reply_to_message.message_id) if message.reply_to_message else None,
            attachments=attachments,
            timestamp=message.date or datetime.utcnow(),
            raw_message=message,
        )

    @classmethod
    def from_discord(cls, message) -> 'NormalizedMessage':
        """Create from discord.Message"""
        text = message.content or ""

        # Detect command
        command = None
        command_args = None
        msg_type = MessageType.TEXT

        if text.startswith('/') or text.startswith('!'):
            msg_type = MessageType.COMMAND
            parts = text.split(None, 1)
            command = parts[0]
            command_args = parts[1] if len(parts) > 1 else None

        # Build sender
        sender = MessageSender(
            id=str(message.author.id),
            username=message.author.name,
            display_name=message.author.display_name,
            is_bot=message.author.bot,
        )

        # Collect attachments
        attachments = []
        for att in message.attachments:
            att_type = "image" if att.content_type and att.content_type.startswith("image") else "file"
            attachments.append(MessageAttachment(
                type=att_type,
                url=att.url,
                filename=att.filename,
                mime_type=att.content_type,
                size_bytes=att.size,
            ))

        return cls(
            id=str(message.id),
            channel="discord",
            conversation_id=str(message.channel.id),
            type=msg_type,
            text=text,
            command=command,
            command_args=command_args,
            sender=sender,
            thread_id=str(message.thread.id) if hasattr(message, 'thread') and message.thread else None,
            attachments=attachments,
            timestamp=message.created_at or datetime.utcnow(),
            raw_message=message,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging/storage"""
        return {
            "id": self.id,
            "channel": self.channel,
            "conversation_id": self.conversation_id,
            "type": self.type.value,
            "text": self.text,
            "command": self.command,
            "sender": {
                "id": self.sender.id,
                "username": self.sender.username,
                "is_bot": self.sender.is_bot,
            },
            "timestamp": self.timestamp.isoformat(),
            "has_attachments": self.has_attachments(),
        }
