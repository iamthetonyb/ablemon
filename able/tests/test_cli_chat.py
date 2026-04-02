from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from able.cli.chat import TerminalApprovalWorkflow, _ReasoningPreview, _WORK_STYLE_OPTIONS, _handle_slash, SlashCtx, build_parser
from able.core.approval.workflow import ApprovalStatus
from able.core.buddy.model import BuddyState
from able.core.gateway.gateway import ABLEGateway


def test_chat_parser_defaults():
    parser = build_parser()
    args = parser.parse_args([])

    assert args.session == "local-cli"
    assert args.client == "master"
    assert args.control_port == 0
    assert args.auto_approve is False
    assert args.verbose is False


def test_work_style_options_include_all_terrain():
    assert any(value == "all-terrain" for value, _description in _WORK_STYLE_OPTIONS)


def test_reasoning_preview_extracts_think_blocks():
    preview = _ReasoningPreview(limit=40)

    thought, answer = preview.consume("<think>plan the steps</think>Final answer")

    assert "plan the steps" in thought
    assert answer == "Final answer"


def test_reasoning_preview_passthrough_without_think():
    preview = _ReasoningPreview()

    thought, answer = preview.consume("Plain answer")

    assert thought == ""
    assert answer == "Plain answer"


def test_terminal_approval_can_auto_approve():
    workflow = TerminalApprovalWorkflow(auto_approve=True)

    result = asyncio.run(
        workflow.request_approval(
            operation="github_create_pr",
            details={"repo": "iamthetonyb/ABLE"},
            requester_id="local-cli",
            risk_level="high",
        )
    )

    assert result.status == ApprovalStatus.APPROVED
    assert "automatically" in (result.reason or "")


def test_resolve_channel_prefers_cli_metadata():
    assert ABLEGateway._resolve_channel(None, {"channel": "cli"}) == "cli"
    assert ABLEGateway._resolve_channel(None, {"source": "cli"}) == "cli"
    assert ABLEGateway._resolve_channel(None, None) == "api"


def test_chat_parser_no_stream_flag():
    parser = build_parser()
    args = parser.parse_args(["--no-stream"])
    assert args.no_stream is True

    args = parser.parse_args([])
    assert args.no_stream is False


def test_chat_parser_verbose_flag():
    parser = build_parser()
    args = parser.parse_args(["--verbose"])
    assert args.verbose is True

    args = parser.parse_args([])
    assert args.verbose is False


def test_gateway_has_stream_message():
    """Verify the gateway exposes a stream_message async generator."""
    assert hasattr(ABLEGateway, "stream_message")
    import inspect
    assert inspect.isasyncgenfunction(ABLEGateway.stream_message)


class _Scanner:
    async def process(self, message, metadata):
        return {"security_verdict": {"passed": True}}


class _Auditor:
    async def process(self, scan_result):
        return {"approved_for_executor": True}


class _TranscriptManager:
    def get_recent_messages(self, target_id, limit=20):
        return []


class _ImmediateFailureChain:
    def __init__(self):
        self.providers = [SimpleNamespace(name="stub-provider")]
        self.complete_called = False

    async def stream(self, msgs, **kwargs):
        raise RuntimeError("boom before chunks")
        yield  # pragma: no cover

    async def complete(self, msgs, **kwargs):
        self.complete_called = True
        return SimpleNamespace(content="full fallback")


class _PartialFailureChain:
    def __init__(self):
        self.providers = [SimpleNamespace(name="stub-provider")]
        self.complete_called = False

    async def stream(self, msgs, **kwargs):
        yield "partial "
        raise RuntimeError("boom after chunk")

    async def complete(self, msgs, **kwargs):
        self.complete_called = True
        return SimpleNamespace(content="should not be used")


def _stub_gateway(chain):
    gateway = object.__new__(ABLEGateway)
    gateway.scanner = _Scanner()
    gateway.auditor = _Auditor()
    gateway.prompt_enricher = None
    gateway.complexity_scorer = None
    gateway.provider_chain = chain
    gateway.interaction_logger = None
    gateway.memory = None
    gateway.transcript_manager = _TranscriptManager()
    gateway.session_mgr = None
    return gateway


@pytest.mark.asyncio
async def test_stream_message_falls_back_only_when_no_chunks_emitted():
    chain = _ImmediateFailureChain()
    gateway = _stub_gateway(chain)

    chunks = [
        chunk
        async for chunk in gateway.stream_message(
            message="hi",
            user_id="cli",
            client_id="master",
            metadata={"channel": "cli"},
        )
    ]

    assert chunks == ["full fallback"]
    assert chain.complete_called is True


@pytest.mark.asyncio
async def test_stream_message_does_not_duplicate_after_partial_output():
    chain = _PartialFailureChain()
    gateway = _stub_gateway(chain)

    chunks = [
        chunk
        async for chunk in gateway.stream_message(
            message="hi",
            user_id="cli",
            client_id="master",
            metadata={"channel": "cli"},
        )
    ]

    assert chunks == ["partial "]
    assert chain.complete_called is False


@pytest.mark.asyncio
async def test_buddy_slash_uses_setup_flow_when_no_active_buddy(monkeypatch):
    created = BuddyState(name="Wave", species="wave")

    async def fake_setup(*args, **kwargs):
        return created

    monkeypatch.setattr("able.cli.chat._buddy_setup_flow", fake_setup)

    ctx = SlashCtx(
        gateway=SimpleNamespace(),
        args=SimpleNamespace(session="local", client="master"),
        load_buddy=lambda: None,
        save_buddy=lambda value: None,
        load_buddy_collection=lambda: None,
        switch_active_buddy=lambda selector: None,
        update_collection_profile=lambda profile: None,
        record_collection_progress=lambda domain, points=1: {"new_buddies": [], "new_badges": [], "easter_egg_unlocked": False},
        STARTER_SPECIES=object(),
        create_starter_buddy=lambda **kwargs: created,
        render_full=lambda current: "full",
        render_banner=lambda current: "banner",
        render_backpack=lambda collection: "bag",
        render_starter_selection=lambda: "starter",
        render_battle_result=lambda *args: "battle",
        render_evolution=lambda *args: "evolution",
        render_legendary_unlock=lambda *args: "legendary",
    )

    handled, buddy = await _handle_slash("/buddy", ctx, None)

    assert handled is True
    assert buddy is created


@pytest.mark.asyncio
async def test_resources_slash_uses_resource_plane_list_resources(monkeypatch, capsys):
    class FakeResourcePlane:
        def list_resources(self):
            return [{"id": "service:able", "kind": "service", "state": "running"}]

    monkeypatch.setattr("able.core.control_plane.resources.ResourcePlane", FakeResourcePlane)

    ctx = SlashCtx(
        gateway=SimpleNamespace(provider_chain=SimpleNamespace(providers=[]), tool_registry=SimpleNamespace(tool_count=0), transcript_manager=SimpleNamespace(get_recent_messages=lambda *_args, **_kwargs: []), session_mgr=None),
        args=SimpleNamespace(session="local", client="master"),
        load_buddy=lambda: None,
        save_buddy=lambda value: None,
        load_buddy_collection=lambda: None,
        switch_active_buddy=lambda selector: None,
        update_collection_profile=lambda profile: None,
        record_collection_progress=lambda domain, points=1: {"new_buddies": [], "new_badges": [], "easter_egg_unlocked": False},
        STARTER_SPECIES=object(),
        create_starter_buddy=lambda **kwargs: None,
        render_full=lambda current: "full",
        render_banner=lambda current: "banner",
        render_backpack=lambda collection: "bag",
        render_starter_selection=lambda: "starter",
        render_battle_result=lambda *args: "battle",
        render_evolution=lambda *args: "evolution",
        render_header=lambda *args: "header",
        render_legendary_unlock=lambda *args: "legendary",
    )

    handled, buddy = await _handle_slash("/resources", ctx, None)

    assert handled is True
    assert buddy is None
    assert "service:able" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_compact_slash_is_handled(monkeypatch, capsys):
    ctx = SlashCtx(
        gateway=SimpleNamespace(
            provider_chain=SimpleNamespace(providers=[SimpleNamespace(name="stub")]),
            tool_registry=SimpleNamespace(tool_count=0),
            transcript_manager=SimpleNamespace(
                get_recent_messages=lambda *_args, **_kwargs: [
                    {"direction": "outbound", "message": "Latest answer"},
                    {"direction": "inbound", "message": "Latest question"},
                ]
            ),
            session_mgr=SimpleNamespace(
                get_or_create=lambda _session: SimpleNamespace(messages=3, total_tokens=120, avg_complexity=0.42)
            ),
        ),
        args=SimpleNamespace(session="local", client="master"),
        load_buddy=lambda: None,
        save_buddy=lambda value: None,
        load_buddy_collection=lambda: None,
        switch_active_buddy=lambda selector: None,
        update_collection_profile=lambda profile: None,
        record_collection_progress=lambda domain, points=1: {"new_buddies": [], "new_badges": [], "easter_egg_unlocked": False},
        STARTER_SPECIES=object(),
        create_starter_buddy=lambda **kwargs: None,
        render_full=lambda current: "full",
        render_banner=lambda current: "banner",
        render_backpack=lambda collection: "bag",
        render_starter_selection=lambda: "starter",
        render_battle_result=lambda *args: "battle",
        render_evolution=lambda *args: "evolution",
        render_header=lambda *args: "header",
        render_legendary_unlock=lambda *args: "legendary",
    )

    handled, buddy = await _handle_slash("/compact", ctx, None)

    assert handled is True
    assert buddy is None
    assert "compacted view" in capsys.readouterr().out
