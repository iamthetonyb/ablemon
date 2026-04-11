"""Tests for F7 — Channel Threading.

Covers: context visibility, reply modes, @mention detection,
should_respond logic, thread context building.
"""

import pytest

from able.channels.threading import (
    ContextVisibility,
    MentionMatch,
    MentionMode,
    ReplyMode,
    ThreadConfig,
    ThreadManager,
)


@pytest.fixture
def manager():
    return ThreadManager()


@pytest.fixture
def group_manager():
    config = ThreadConfig(
        group_mode=True,
        bot_names=["able", "ABLE"],
    )
    return ThreadManager(config)


def _make_msgs(n, thread_id="t1"):
    return [
        {"text": f"msg {i}", "sender": f"user-{i % 3}", "thread_id": thread_id}
        for i in range(n)
    ]


# ── Context visibility ──────────────────────────────────────────

class TestContextVisibility:

    def test_full_visibility(self):
        config = ThreadConfig(context_visibility=ContextVisibility.FULL)
        mgr = ThreadManager(config)
        msgs = _make_msgs(10)
        ctx = mgr.build_context("t1", "telegram", msgs)
        assert len(ctx.messages) == 10

    def test_thread_only(self, manager):
        msgs = _make_msgs(5, "t1") + _make_msgs(3, "t2")
        ctx = manager.build_context("t1", "telegram", msgs)
        assert len(ctx.messages) == 5  # Only t1 messages

    def test_mention_only(self):
        config = ThreadConfig(
            context_visibility=ContextVisibility.MENTION_ONLY,
            bot_names=["able"],
        )
        mgr = ThreadManager(config)
        msgs = [
            {"text": "hello @able", "sender": "u1", "thread_id": "t1"},
            {"text": "general chat", "sender": "u2", "thread_id": "t1"},
            {"text": "@ABLE help", "sender": "u3", "thread_id": "t1"},
        ]
        ctx = mgr.build_context("t1", "telegram", msgs)
        assert len(ctx.messages) == 2  # Only mentions

    def test_none_visibility(self):
        config = ThreadConfig(context_visibility=ContextVisibility.NONE)
        mgr = ThreadManager(config)
        ctx = mgr.build_context("t1", "telegram", _make_msgs(10))
        assert len(ctx.messages) == 0

    def test_max_context_messages(self):
        config = ThreadConfig(
            context_visibility=ContextVisibility.FULL,
            max_context_messages=5,
        )
        mgr = ThreadManager(config)
        ctx = mgr.build_context("t1", "telegram", _make_msgs(20))
        assert len(ctx.messages) == 5


# ── Thread context ──────────────────────────────────────────────

class TestThreadContext:

    def test_participants_tracked(self, manager):
        msgs = _make_msgs(6, "t1")
        ctx = manager.build_context("t1", "telegram", msgs)
        assert len(ctx.participants) == 3  # user-0, user-1, user-2

    def test_channel_recorded(self, manager):
        ctx = manager.build_context("t1", "discord", [])
        assert ctx.channel == "discord"


# ── Should respond ──────────────────────────────────────────────

class TestShouldRespond:

    def test_dm_always_responds(self, manager):
        assert manager.should_respond({"text": "hello"})

    def test_group_ignores_general(self, group_manager):
        assert not group_manager.should_respond({"text": "hello everyone"})

    def test_group_responds_to_mention(self, group_manager):
        assert group_manager.should_respond({"text": "hey @able help me"})

    def test_group_responds_in_known_thread(self, group_manager):
        # First build context so thread is tracked
        group_manager.build_context("t1", "telegram", _make_msgs(1))
        assert group_manager.should_respond({"text": "follow up", "thread_id": "t1"})

    def test_group_responds_to_reply(self, group_manager):
        assert group_manager.should_respond({
            "text": "thanks",
            "reply_to_bot": True,
        })


# ── Reply mode selection ────────────────────────────────────────

class TestReplyMode:

    def test_default_reply_mode(self, manager):
        mode = manager.select_reply_mode({"text": "hello"})
        assert mode == ReplyMode.THREAD  # Default

    def test_group_thread_reply(self, group_manager):
        mode = group_manager.select_reply_mode({
            "text": "hello", "thread_id": "t1",
        })
        assert mode == ReplyMode.THREAD

    def test_group_mention_to_thread(self, group_manager):
        mode = group_manager.select_reply_mode({"text": "@able help"})
        assert mode == ReplyMode.THREAD  # RESPOND_IN_THREAD is default

    def test_mention_dm_mode(self):
        config = ThreadConfig(
            group_mode=True,
            mention_mode=MentionMode.RESPOND_DM,
            bot_names=["able"],
        )
        mgr = ThreadManager(config)
        mode = mgr.select_reply_mode({"text": "@able private"})
        assert mode == ReplyMode.DIRECT


# ── Mention extraction ──────────────────────────────────────────

class TestMentions:

    def test_extract_mentions(self, manager):
        mentions = manager.extract_mentions("hello @able and @bob")
        assert len(mentions) == 2
        assert isinstance(mentions[0], MentionMatch)

    def test_bot_mention_flagged(self, manager):
        mentions = manager.extract_mentions("hey @able")
        assert mentions[0].is_bot
        assert mentions[0].username == "able"

    def test_non_bot_mention(self, manager):
        mentions = manager.extract_mentions("hey @bob")
        assert not mentions[0].is_bot

    def test_no_mentions(self, manager):
        mentions = manager.extract_mentions("plain text")
        assert len(mentions) == 0

    def test_mention_position(self, manager):
        mentions = manager.extract_mentions("hi @able there")
        assert mentions[0].position == 3
