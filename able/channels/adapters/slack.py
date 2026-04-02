"""
Slack Channel Adapter

Implementation for Slack Bot API.
"""

import asyncio
import logging
from typing import Optional, List

from .base import ChannelAdapter
from ..normalized_message import NormalizedMessage, MessageSender, MessageType

logger = logging.getLogger(__name__)


class SlackAdapter(ChannelAdapter):
    """
    Slack Bot API adapter.

    Features:
    - Channel messages
    - Thread support
    - Reactions
    - Message editing
    - Block Kit support
    """

    def __init__(
        self,
        bot_token: str,
        app_token: str = None,  # For Socket Mode
        allowed_channels: List[str] = None
    ):
        super().__init__("slack")
        self.bot_token = bot_token
        self.app_token = app_token
        self.allowed_channels = allowed_channels
        self._client = None
        self._socket_client = None

    async def connect(self):
        """Start the Slack bot"""
        try:
            from slack_sdk.web.async_client import AsyncWebClient
            from slack_sdk.socket_mode.aiohttp import SocketModeClient
            from slack_sdk.socket_mode.request import SocketModeRequest
            from slack_sdk.socket_mode.response import SocketModeResponse

            self._client = AsyncWebClient(token=self.bot_token)

            if self.app_token:
                # Socket mode for real-time events
                self._socket_client = SocketModeClient(
                    app_token=self.app_token,
                    web_client=self._client
                )

                async def handle_events(client: SocketModeClient, req: SocketModeRequest):
                    if req.type == "events_api":
                        event = req.payload.get("event", {})

                        if event.get("type") == "message" and not event.get("bot_id"):
                            # Check channel restrictions
                            channel = event.get("channel")
                            if self.allowed_channels and channel not in self.allowed_channels:
                                return

                            msg = self._normalize_slack_message(event)
                            await self._dispatch_message(msg)

                    # Acknowledge the event
                    response = SocketModeResponse(envelope_id=req.envelope_id)
                    await client.send_socket_mode_response(response)

                self._socket_client.socket_mode_request_listeners.append(handle_events)
                asyncio.create_task(self._socket_client.connect())

            self._connected = True
            logger.info("Slack adapter connected")

        except ImportError:
            logger.error("slack_sdk not installed")
            raise

    async def disconnect(self):
        """Stop the Slack bot"""
        if self._socket_client:
            await self._socket_client.close()
        self._connected = False
        logger.info("Slack adapter disconnected")

    def _normalize_slack_message(self, event: dict) -> NormalizedMessage:
        """Convert Slack event to NormalizedMessage"""
        text = event.get("text", "")

        # Detect commands (slash commands or mentions)
        command = None
        msg_type = MessageType.TEXT

        if text.startswith("/"):
            msg_type = MessageType.COMMAND
            parts = text.split(None, 1)
            command = parts[0]

        sender = MessageSender(
            id=event.get("user", "unknown"),
            is_bot=bool(event.get("bot_id")),
        )

        return NormalizedMessage(
            id=event.get("ts", ""),
            channel="slack",
            conversation_id=event.get("channel", ""),
            type=msg_type,
            text=text,
            command=command,
            sender=sender,
            thread_id=event.get("thread_ts"),
            metadata={"raw_event": event}
        )

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

        response = await self._client.chat_postMessage(
            channel=conversation_id,
            text=text,
            thread_ts=reply_to,
            mrkdwn=kwargs.get('mrkdwn', True)
        )
        return response.get("ts", "")

    async def send_typing(self, conversation_id: str):
        """Slack doesn't have typing indicators for bots"""
        pass

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

        await self._client.chat_update(
            channel=conversation_id,
            ts=message_id,
            text=text
        )

    async def delete_message(
        self,
        conversation_id: str,
        message_id: str
    ):
        """Delete a message"""
        if not self._client:
            raise RuntimeError("Client not connected")

        await self._client.chat_delete(
            channel=conversation_id,
            ts=message_id
        )

    async def send_with_buttons(
        self,
        conversation_id: str,
        text: str,
        buttons: List[List[dict]],
        **kwargs
    ) -> str:
        """Send message with Block Kit buttons"""
        if not self._client:
            raise RuntimeError("Client not connected")

        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": text
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": btn.get("text", btn.get("label", "Button"))
                        },
                        "action_id": btn.get("callback_data", btn.get("data", f"action_{i}"))
                    }
                    for row in buttons
                    for i, btn in enumerate(row)
                ]
            }
        ]

        response = await self._client.chat_postMessage(
            channel=conversation_id,
            text=text,  # Fallback
            blocks=blocks
        )
        return response.get("ts", "")

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

        response = await self._client.files_upload_v2(
            channel=conversation_id,
            file=file_path,
            initial_comment=caption
        )
        return response.get("file", {}).get("id", "")

    async def react(
        self,
        conversation_id: str,
        message_id: str,
        emoji: str
    ):
        """Add a reaction"""
        if not self._client:
            return

        try:
            # Remove colons if present
            emoji = emoji.strip(":")
            await self._client.reactions_add(
                channel=conversation_id,
                timestamp=message_id,
                name=emoji
            )
        except Exception as e:
            logger.debug(f"Failed to add reaction: {e}")
