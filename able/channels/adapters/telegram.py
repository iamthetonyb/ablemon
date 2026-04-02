"""
Telegram Channel Adapter

Full implementation for Telegram Bot API.
"""

import asyncio
import logging
from typing import Optional, List, Any

from .base import ChannelAdapter
from ..normalized_message import NormalizedMessage

logger = logging.getLogger(__name__)


class TelegramAdapter(ChannelAdapter):
    """
    Telegram Bot API adapter.

    Features:
    - Full message support (text, commands, files)
    - Inline keyboards
    - Message editing
    - Typing indicators
    - Callback query handling
    """

    def __init__(
        self,
        token: str,
        allowed_users: List[int] = None,
        parse_mode: str = "Markdown"
    ):
        super().__init__("telegram")
        self.token = token
        self.allowed_users = allowed_users or []
        self.parse_mode = parse_mode
        self._app = None
        self._bot = None

    async def connect(self):
        """Start the Telegram bot"""
        try:
            from telegram.ext import (
                Application,
                MessageHandler,
                CallbackQueryHandler,
                filters
            )

            self._app = Application.builder().token(self.token).build()

            # Register handlers
            self._app.add_handler(MessageHandler(
                filters.ALL & ~filters.COMMAND,
                self._handle_message
            ))
            self._app.add_handler(MessageHandler(
                filters.COMMAND,
                self._handle_command
            ))
            self._app.add_handler(CallbackQueryHandler(
                self._handle_callback
            ))

            self._bot = self._app.bot
            self._connected = True

            # Start polling in background
            await self._app.initialize()
            await self._app.start()
            asyncio.create_task(self._app.updater.start_polling())

            logger.info("Telegram adapter connected")

        except ImportError:
            logger.error("python-telegram-bot not installed")
            raise

    async def disconnect(self):
        """Stop the Telegram bot"""
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            self._connected = False
            logger.info("Telegram adapter disconnected")

    async def _handle_message(self, update, context):
        """Handle incoming messages"""
        if not self._check_allowed(update.effective_user.id):
            return

        msg = NormalizedMessage.from_telegram(update.message)
        await self._dispatch_message(msg)

    async def _handle_command(self, update, context):
        """Handle command messages"""
        if not self._check_allowed(update.effective_user.id):
            return

        msg = NormalizedMessage.from_telegram(update.message)
        await self._dispatch_message(msg)

    async def _handle_callback(self, update, context):
        """Handle callback queries from inline buttons"""
        if not self._check_allowed(update.effective_user.id):
            await update.callback_query.answer("Unauthorized")
            return

        await self._dispatch_callback(update.callback_query)

    def _check_allowed(self, user_id: int) -> bool:
        """Check if user is allowed"""
        if not self.allowed_users:
            return True  # No restrictions
        return user_id in self.allowed_users

    async def send(
        self,
        conversation_id: str,
        text: str,
        reply_to: Optional[str] = None,
        **kwargs
    ) -> str:
        """Send a message"""
        if not self._bot:
            raise RuntimeError("Bot not connected")

        message = await self._bot.send_message(
            chat_id=int(conversation_id),
            text=text,
            parse_mode=kwargs.get('parse_mode', self.parse_mode),
            reply_to_message_id=int(reply_to) if reply_to else None,
            disable_notification=kwargs.get('silent', False)
        )
        return str(message.message_id)

    async def send_typing(self, conversation_id: str):
        """Send typing indicator"""
        if self._bot:
            await self._bot.send_chat_action(
                chat_id=int(conversation_id),
                action="typing"
            )

    async def edit_message(
        self,
        conversation_id: str,
        message_id: str,
        text: str,
        **kwargs
    ):
        """Edit a message"""
        if not self._bot:
            raise RuntimeError("Bot not connected")

        await self._bot.edit_message_text(
            chat_id=int(conversation_id),
            message_id=int(message_id),
            text=text,
            parse_mode=kwargs.get('parse_mode', self.parse_mode)
        )

    async def delete_message(
        self,
        conversation_id: str,
        message_id: str
    ):
        """Delete a message"""
        if not self._bot:
            raise RuntimeError("Bot not connected")

        await self._bot.delete_message(
            chat_id=int(conversation_id),
            message_id=int(message_id)
        )

    async def send_with_buttons(
        self,
        conversation_id: str,
        text: str,
        buttons: List[List[dict]],
        **kwargs
    ) -> str:
        """Send message with inline keyboard"""
        if not self._bot:
            raise RuntimeError("Bot not connected")

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    btn.get('text', btn.get('label', 'Button')),
                    callback_data=btn.get('callback_data', btn.get('data', ''))
                )
                for btn in row
            ]
            for row in buttons
        ])

        message = await self._bot.send_message(
            chat_id=int(conversation_id),
            text=text,
            parse_mode=kwargs.get('parse_mode', self.parse_mode),
            reply_markup=keyboard
        )
        return str(message.message_id)

    async def send_file(
        self,
        conversation_id: str,
        file_path: str,
        caption: Optional[str] = None,
        **kwargs
    ) -> str:
        """Send a file"""
        if not self._bot:
            raise RuntimeError("Bot not connected")

        with open(file_path, 'rb') as f:
            message = await self._bot.send_document(
                chat_id=int(conversation_id),
                document=f,
                caption=caption,
                parse_mode=kwargs.get('parse_mode', self.parse_mode)
            )
        return str(message.message_id)

    async def react(
        self,
        conversation_id: str,
        message_id: str,
        emoji: str
    ):
        """Add reaction (requires Telegram Premium for some chats)"""
        if not self._bot:
            return

        try:
            from telegram import ReactionTypeEmoji
            await self._bot.set_message_reaction(
                chat_id=int(conversation_id),
                message_id=int(message_id),
                reaction=[ReactionTypeEmoji(emoji)]
            )
        except Exception as e:
            logger.debug(f"Reaction not supported: {e}")
