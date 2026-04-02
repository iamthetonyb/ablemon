"""
Unified Gateway - Channel-agnostic message routing.

PicoClaw-inspired: Single agent instance bridges multiple platforms.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any, Awaitable

from .adapters.base import ChannelAdapter
from .normalized_message import NormalizedMessage

logger = logging.getLogger(__name__)


@dataclass
class ChannelConfig:
    """Configuration for a channel"""
    adapter: ChannelAdapter
    enabled: bool = True
    priority: int = 0  # Higher = higher priority for routing
    rate_limit: int = 0  # Messages per minute, 0 = unlimited
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    """Result from processing pipeline"""
    success: bool
    response: Optional[str] = None
    error: Optional[str] = None
    should_reply: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


class UnifiedGateway:
    """
    Unified multi-channel gateway.

    Features:
    - Register multiple channel adapters
    - Normalize messages from all channels
    - Route through single processing pipeline
    - Send responses back via original channel
    """

    def __init__(self):
        self.channels: Dict[str, ChannelConfig] = {}
        self.message_queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        self._processor: Optional[Callable[[NormalizedMessage], Awaitable[PipelineResult]]] = None
        self._middleware: List[Callable[[NormalizedMessage], Awaitable[Optional[NormalizedMessage]]]] = []

    def register_channel(
        self,
        name: str,
        adapter: ChannelAdapter,
        enabled: bool = True,
        priority: int = 0,
        rate_limit: int = 0
    ):
        """
        Register a channel adapter.

        Args:
            name: Channel name (telegram, discord, slack, etc.)
            adapter: ChannelAdapter implementation
            enabled: Whether channel is active
            priority: Routing priority
            rate_limit: Messages per minute limit
        """
        config = ChannelConfig(
            adapter=adapter,
            enabled=enabled,
            priority=priority,
            rate_limit=rate_limit
        )

        # Set up message handler
        adapter.on_message(lambda msg: self._enqueue_message(msg))

        self.channels[name] = config
        logger.info(f"Registered channel: {name}")

    def set_processor(
        self,
        processor: Callable[[NormalizedMessage], Awaitable[PipelineResult]]
    ):
        """
        Set the message processing function.

        This is called for every incoming message after middleware.

        Args:
            processor: Async function that takes NormalizedMessage and returns PipelineResult
        """
        self._processor = processor

    def add_middleware(
        self,
        middleware: Callable[[NormalizedMessage], Awaitable[Optional[NormalizedMessage]]]
    ):
        """
        Add middleware to the processing pipeline.

        Middleware can:
        - Transform messages
        - Filter messages (return None to skip)
        - Add metadata

        Args:
            middleware: Async function that takes and returns NormalizedMessage
        """
        self._middleware.append(middleware)

    async def _enqueue_message(self, message: NormalizedMessage):
        """Add message to processing queue"""
        await self.message_queue.put(message)

    async def start(self):
        """Start the gateway and all enabled channels"""
        self._running = True

        # Connect all enabled channels
        for name, config in self.channels.items():
            if config.enabled:
                try:
                    await config.adapter.connect()
                    logger.info(f"Connected channel: {name}")
                except Exception as e:
                    logger.error(f"Failed to connect {name}: {e}")

        # Start message processing loop
        asyncio.create_task(self._process_messages())

        logger.info("Unified gateway started")

    async def stop(self):
        """Stop the gateway and all channels"""
        self._running = False

        for name, config in self.channels.items():
            try:
                await config.adapter.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting {name}: {e}")

        logger.info("Unified gateway stopped")

    async def _process_messages(self):
        """Main message processing loop"""
        while self._running:
            try:
                # Wait for message with timeout
                try:
                    message = await asyncio.wait_for(
                        self.message_queue.get(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                # Process in background to not block queue
                asyncio.create_task(self._handle_message(message))

            except Exception as e:
                logger.error(f"Error in message processing loop: {e}")

    async def _handle_message(self, message: NormalizedMessage):
        """Handle a single message through the pipeline"""
        try:
            # Run through middleware
            processed = message
            for middleware in self._middleware:
                processed = await middleware(processed)
                if processed is None:
                    logger.debug(f"Message filtered by middleware: {message.id}")
                    return

            # Send typing indicator
            channel_config = self.channels.get(processed.channel)
            if channel_config:
                await channel_config.adapter.send_typing(processed.conversation_id)

            # Process message
            if self._processor:
                result = await self._processor(processed)

                # Send response
                if result.should_reply and result.response:
                    await self.send_response(processed, result.response)

                if result.error:
                    logger.warning(f"Processing error: {result.error}")
            else:
                logger.warning("No processor set, message ignored")

        except Exception as e:
            logger.exception(f"Error handling message: {e}")
            # Try to send error response
            try:
                await self.send_response(
                    message,
                    f"An error occurred while processing your message."
                )
            except Exception:
                pass

    async def send_response(
        self,
        original: NormalizedMessage,
        text: str,
        reply: bool = True
    ):
        """
        Send a response to a message.

        Args:
            original: Original message to respond to
            text: Response text
            reply: Whether to reply directly to the message
        """
        channel_config = self.channels.get(original.channel)
        if not channel_config:
            logger.error(f"Channel not found: {original.channel}")
            return

        await channel_config.adapter.send(
            conversation_id=original.conversation_id,
            text=text,
            reply_to=original.id if reply else None
        )

    async def broadcast(
        self,
        text: str,
        channels: List[str] = None,
        conversation_ids: Dict[str, str] = None
    ):
        """
        Broadcast a message to multiple channels.

        Args:
            text: Message text
            channels: List of channel names (or all if None)
            conversation_ids: Map of channel -> conversation_id
        """
        target_channels = channels or list(self.channels.keys())

        for name in target_channels:
            config = self.channels.get(name)
            if not config or not config.enabled:
                continue

            conv_id = conversation_ids.get(name) if conversation_ids else None
            if not conv_id:
                logger.warning(f"No conversation_id for broadcast to {name}")
                continue

            try:
                await config.adapter.send(conv_id, text)
            except Exception as e:
                logger.error(f"Failed to broadcast to {name}: {e}")

    def get_channel_status(self) -> Dict[str, Dict]:
        """Get status of all channels"""
        return {
            name: {
                "enabled": config.enabled,
                "connected": config.adapter.is_connected,
                "priority": config.priority,
            }
            for name, config in self.channels.items()
        }

    async def send_to_channel(
        self,
        channel: str,
        conversation_id: str,
        text: str,
        **kwargs
    ) -> Optional[str]:
        """
        Send a message to a specific channel.

        Args:
            channel: Channel name
            conversation_id: Target conversation
            text: Message text
            **kwargs: Channel-specific options

        Returns:
            Message ID if successful
        """
        config = self.channels.get(channel)
        if not config:
            logger.error(f"Channel not found: {channel}")
            return None

        if not config.enabled or not config.adapter.is_connected:
            logger.warning(f"Channel not available: {channel}")
            return None

        return await config.adapter.send(conversation_id, text, **kwargs)
