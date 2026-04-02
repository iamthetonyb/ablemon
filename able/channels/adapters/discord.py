"""
Discord Channel Adapter

Implementation for Discord Bot API.
"""

import asyncio
import logging
from typing import Optional, List

from .base import ChannelAdapter
from ..normalized_message import NormalizedMessage

logger = logging.getLogger(__name__)


class DiscordAdapter(ChannelAdapter):
    """
    Discord Bot API adapter.

    Features:
    - Text channel support
    - Thread support
    - Reactions
    - Message editing
    - File uploads
    """

    def __init__(
        self,
        token: str,
        guild_ids: List[int] = None,
        allowed_channels: List[int] = None,
        command_prefix: str = "/"
    ):
        super().__init__("discord")
        self.token = token
        self.guild_ids = guild_ids
        self.allowed_channels = allowed_channels
        self.command_prefix = command_prefix
        self._client = None

    async def connect(self):
        """Start the Discord bot"""
        try:
            import discord
            from discord import Intents

            intents = Intents.default()
            intents.message_content = True
            intents.guilds = True

            self._client = discord.Client(intents=intents)

            @self._client.event
            async def on_ready():
                logger.info(f"Discord connected as {self._client.user}")
                self._connected = True

            @self._client.event
            async def on_message(message):
                # Ignore own messages
                if message.author == self._client.user:
                    return

                # Check channel restrictions
                if self.allowed_channels and message.channel.id not in self.allowed_channels:
                    return

                msg = NormalizedMessage.from_discord(message)
                await self._dispatch_message(msg)

            # Start in background
            asyncio.create_task(self._client.start(self.token))

            # Wait for connection
            for _ in range(30):
                if self._connected:
                    break
                await asyncio.sleep(1)

        except ImportError:
            logger.error("discord.py not installed")
            raise

    async def disconnect(self):
        """Stop the Discord bot"""
        if self._client:
            await self._client.close()
            self._connected = False
            logger.info("Discord adapter disconnected")

    async def send(
        self,
        conversation_id: str,
        text: str,
        reply_to: Optional[str] = None,
        **kwargs
    ) -> str:
        """Send a message"""
        if not self._client:
            raise RuntimeError("Client not connected")

        channel = self._client.get_channel(int(conversation_id))
        if not channel:
            raise ValueError(f"Channel not found: {conversation_id}")

        # Get reference for reply
        reference = None
        if reply_to:
            try:
                original = await channel.fetch_message(int(reply_to))
                reference = original
            except Exception:
                pass

        message = await channel.send(text, reference=reference)
        return str(message.id)

    async def send_typing(self, conversation_id: str):
        """Send typing indicator"""
        if not self._client:
            return

        channel = self._client.get_channel(int(conversation_id))
        if channel:
            await channel.typing()

    async def edit_message(
        self,
        conversation_id: str,
        message_id: str,
        text: str,
        **kwargs
    ):
        """Edit a message"""
        if not self._client:
            raise RuntimeError("Client not connected")

        channel = self._client.get_channel(int(conversation_id))
        if not channel:
            return

        try:
            message = await channel.fetch_message(int(message_id))
            await message.edit(content=text)
        except Exception as e:
            logger.error(f"Failed to edit message: {e}")

    async def delete_message(
        self,
        conversation_id: str,
        message_id: str
    ):
        """Delete a message"""
        if not self._client:
            raise RuntimeError("Client not connected")

        channel = self._client.get_channel(int(conversation_id))
        if not channel:
            return

        try:
            message = await channel.fetch_message(int(message_id))
            await message.delete()
        except Exception as e:
            logger.error(f"Failed to delete message: {e}")

    async def send_with_buttons(
        self,
        conversation_id: str,
        text: str,
        buttons: List[List[dict]],
        **kwargs
    ) -> str:
        """Send message with buttons (using Discord views)"""
        if not self._client:
            raise RuntimeError("Client not connected")

        try:
            import discord
            from discord.ui import View, Button

            view = View()
            for row in buttons:
                for btn in row:
                    button = Button(
                        label=btn.get('text', btn.get('label', 'Button')),
                        custom_id=btn.get('callback_data', btn.get('data', '')),
                        style=discord.ButtonStyle.primary
                    )
                    view.add_item(button)

            channel = self._client.get_channel(int(conversation_id))
            if channel:
                message = await channel.send(text, view=view)
                return str(message.id)

        except Exception as e:
            logger.error(f"Failed to send with buttons: {e}")
            # Fallback to plain text
            return await self.send(conversation_id, text)

        return ""

    async def send_file(
        self,
        conversation_id: str,
        file_path: str,
        caption: Optional[str] = None,
        **kwargs
    ) -> str:
        """Send a file"""
        if not self._client:
            raise RuntimeError("Client not connected")

        try:
            import discord

            channel = self._client.get_channel(int(conversation_id))
            if channel:
                with open(file_path, 'rb') as f:
                    file = discord.File(f)
                    message = await channel.send(content=caption, file=file)
                    return str(message.id)
        except Exception as e:
            logger.error(f"Failed to send file: {e}")

        return ""

    async def react(
        self,
        conversation_id: str,
        message_id: str,
        emoji: str
    ):
        """Add a reaction"""
        if not self._client:
            return

        channel = self._client.get_channel(int(conversation_id))
        if not channel:
            return

        try:
            message = await channel.fetch_message(int(message_id))
            await message.add_reaction(emoji)
        except Exception as e:
            logger.debug(f"Failed to add reaction: {e}")
