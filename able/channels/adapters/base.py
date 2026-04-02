"""
Base Channel Adapter

Abstract interface for all channel implementations.
"""

from abc import ABC, abstractmethod
from typing import Callable, Optional, List, Any, Awaitable
from ..normalized_message import NormalizedMessage


class ChannelAdapter(ABC):
    """
    Abstract base class for channel adapters.

    Each adapter handles:
    - Connecting to the platform
    - Receiving messages and converting to NormalizedMessage
    - Sending responses back to the platform
    - Platform-specific features (reactions, threads, etc.)
    """

    def __init__(self, name: str):
        self.name = name
        self._message_handlers: List[Callable[[NormalizedMessage], Awaitable[None]]] = []
        self._callback_handlers: List[Callable[[Any], Awaitable[None]]] = []
        self._connected = False

    @abstractmethod
    async def connect(self):
        """Connect to the platform"""
        pass

    @abstractmethod
    async def disconnect(self):
        """Disconnect from the platform"""
        pass

    @abstractmethod
    async def send(
        self,
        conversation_id: str,
        text: str,
        reply_to: Optional[str] = None,
        **kwargs
    ) -> str:
        """
        Send a message.

        Args:
            conversation_id: Where to send (chat_id, channel_id, etc.)
            text: Message text
            reply_to: Message ID to reply to
            **kwargs: Platform-specific options

        Returns:
            Message ID of sent message
        """
        pass

    @abstractmethod
    async def send_typing(self, conversation_id: str):
        """Send typing indicator"""
        pass

    @abstractmethod
    async def edit_message(
        self,
        conversation_id: str,
        message_id: str,
        text: str,
        **kwargs
    ):
        """Edit a previously sent message"""
        pass

    @abstractmethod
    async def delete_message(
        self,
        conversation_id: str,
        message_id: str
    ):
        """Delete a message"""
        pass

    def on_message(self, handler: Callable[[NormalizedMessage], Awaitable[None]]):
        """Register a message handler"""
        self._message_handlers.append(handler)

    def on_callback(self, handler: Callable[[Any], Awaitable[None]]):
        """Register a callback handler (for inline buttons, etc.)"""
        self._callback_handlers.append(handler)

    async def _dispatch_message(self, message: NormalizedMessage):
        """Dispatch message to all handlers"""
        for handler in self._message_handlers:
            try:
                await handler(message)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Handler error: {e}")

    async def _dispatch_callback(self, callback: Any):
        """Dispatch callback to all handlers"""
        for handler in self._callback_handlers:
            try:
                await handler(callback)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Callback handler error: {e}")

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def send_with_buttons(
        self,
        conversation_id: str,
        text: str,
        buttons: List[List[dict]],
        **kwargs
    ) -> str:
        """
        Send a message with inline buttons.

        Default implementation sends plain text.
        Override for platform-specific button support.

        Args:
            conversation_id: Where to send
            text: Message text
            buttons: Button grid [[{text, callback_data}, ...], ...]
        """
        # Default: ignore buttons, just send text
        return await self.send(conversation_id, text, **kwargs)

    async def send_file(
        self,
        conversation_id: str,
        file_path: str,
        caption: Optional[str] = None,
        **kwargs
    ) -> str:
        """
        Send a file.

        Default implementation sends caption only.
        Override for platform-specific file support.
        """
        text = caption or f"[File: {file_path}]"
        return await self.send(conversation_id, text, **kwargs)

    async def react(
        self,
        conversation_id: str,
        message_id: str,
        emoji: str
    ):
        """
        Add a reaction to a message.

        Default implementation does nothing.
        Override for platform-specific reaction support.
        """
        pass
