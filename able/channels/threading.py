"""
F7 — Channel Threading.

Controls context visibility in group threads, reply-to modes,
and @mention handling across channel adapters.

Usage:
    config = ThreadConfig(
        context_visibility=ContextVisibility.THREAD_ONLY,
        reply_mode=ReplyMode.QUOTE,
        mention_mode=MentionMode.RESPOND_IN_THREAD,
    )

    manager = ThreadManager(config)
    ctx = manager.build_context(thread_id="t123", channel="telegram")
    should_respond = manager.should_respond(message)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class ContextVisibility(str, Enum):
    """What context is shared in group threads."""
    FULL = "full"              # All channel history visible
    THREAD_ONLY = "thread_only"  # Only messages in this thread
    MENTION_ONLY = "mention_only"  # Only messages mentioning the bot
    NONE = "none"              # No historical context


class ReplyMode(str, Enum):
    """How replies reference prior messages."""
    INLINE = "inline"          # Reply in the same flow
    QUOTE = "quote"            # Quote the original message
    THREAD = "thread"          # Reply in a thread (if supported)
    DIRECT = "direct"          # DM the user instead


class MentionMode(str, Enum):
    """How @mentions are handled."""
    RESPOND_IN_THREAD = "respond_in_thread"
    RESPOND_INLINE = "respond_inline"
    RESPOND_DM = "respond_dm"
    IGNORE = "ignore"


@dataclass
class ThreadConfig:
    """Configuration for thread behavior."""
    context_visibility: ContextVisibility = ContextVisibility.THREAD_ONLY
    reply_mode: ReplyMode = ReplyMode.THREAD
    mention_mode: MentionMode = MentionMode.RESPOND_IN_THREAD
    max_context_messages: int = 50
    bot_names: List[str] = field(default_factory=lambda: ["able", "ABLE"])
    group_mode: bool = False  # True when in group chat


@dataclass
class ThreadContext:
    """Context for a specific thread."""
    thread_id: str
    channel: str
    messages: List[Dict[str, Any]] = field(default_factory=list)
    participants: Set[str] = field(default_factory=set)
    is_group: bool = False
    mention_count: int = 0


@dataclass
class MentionMatch:
    """A detected @mention in a message."""
    raw: str
    username: str
    is_bot: bool = False
    position: int = 0


class ThreadManager:
    """Manages thread context and response routing.

    Handles:
    - Context scoping (what messages are visible in a thread)
    - Reply mode selection (inline, quote, thread, DM)
    - @mention detection and response routing
    """

    def __init__(self, config: Optional[ThreadConfig] = None):
        self._config = config or ThreadConfig()
        self._threads: Dict[str, ThreadContext] = {}

    @property
    def config(self) -> ThreadConfig:
        return self._config

    def build_context(
        self,
        thread_id: str,
        channel: str,
        all_messages: Optional[List[Dict[str, Any]]] = None,
    ) -> ThreadContext:
        """Build context for a thread based on visibility settings.

        Args:
            thread_id: The thread identifier.
            channel: Channel name (telegram, discord, etc.).
            all_messages: Full message history (filtered by visibility).

        Returns:
            ThreadContext with filtered messages.
        """
        all_messages = all_messages or []

        if self._config.context_visibility == ContextVisibility.FULL:
            messages = all_messages[-self._config.max_context_messages:]
        elif self._config.context_visibility == ContextVisibility.THREAD_ONLY:
            messages = [
                m for m in all_messages
                if m.get("thread_id") == thread_id
            ][-self._config.max_context_messages:]
        elif self._config.context_visibility == ContextVisibility.MENTION_ONLY:
            messages = [
                m for m in all_messages
                if self._contains_mention(m.get("text", ""))
            ][-self._config.max_context_messages:]
        else:  # NONE
            messages = []

        participants = {m.get("sender", "") for m in messages if m.get("sender")}

        ctx = ThreadContext(
            thread_id=thread_id,
            channel=channel,
            messages=messages,
            participants=participants,
            is_group=self._config.group_mode,
        )
        self._threads[thread_id] = ctx
        return ctx

    def should_respond(self, message: Dict[str, Any]) -> bool:
        """Determine if the bot should respond to a message.

        In group mode:
        - Always respond to @mentions
        - Always respond in threads we're participating in
        - Don't respond to general chat unless mentioned

        In DM mode:
        - Always respond
        """
        if not self._config.group_mode:
            return True  # Always respond in DMs

        text = message.get("text", "")

        # Check for @mention
        if self._contains_mention(text):
            return True

        # Check if we're in this thread
        thread_id = message.get("thread_id", "")
        if thread_id and thread_id in self._threads:
            return True

        # Check if it's a reply to our message
        if message.get("reply_to_bot", False):
            return True

        return False

    def select_reply_mode(self, message: Dict[str, Any]) -> ReplyMode:
        """Select the appropriate reply mode for a message."""
        # In group threads, prefer thread mode
        if self._config.group_mode:
            thread_id = message.get("thread_id", "")
            if thread_id:
                return ReplyMode.THREAD

            # Mentioned → follow mention mode
            if self._contains_mention(message.get("text", "")):
                mode_map = {
                    MentionMode.RESPOND_IN_THREAD: ReplyMode.THREAD,
                    MentionMode.RESPOND_INLINE: ReplyMode.INLINE,
                    MentionMode.RESPOND_DM: ReplyMode.DIRECT,
                    MentionMode.IGNORE: ReplyMode.INLINE,
                }
                return mode_map.get(
                    self._config.mention_mode, ReplyMode.THREAD
                )

        return self._config.reply_mode

    def extract_mentions(self, text: str) -> List[MentionMatch]:
        """Extract @mentions from message text."""
        mentions = []
        for match in re.finditer(r"@(\w+)", text):
            username = match.group(1)
            is_bot = username.lower() in [
                n.lower() for n in self._config.bot_names
            ]
            mentions.append(MentionMatch(
                raw=match.group(0),
                username=username,
                is_bot=is_bot,
                position=match.start(),
            ))
        return mentions

    def _contains_mention(self, text: str) -> bool:
        """Check if text contains an @mention of the bot."""
        text_lower = text.lower()
        return any(
            f"@{name.lower()}" in text_lower
            for name in self._config.bot_names
        )
