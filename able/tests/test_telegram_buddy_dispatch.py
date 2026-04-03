from types import SimpleNamespace

import pytest

from able.core.gateway import gateway as gateway_module
from able.core.gateway.gateway import ABLEGateway
from able.core.providers.base import CompletionResult, ToolCall, UsageStats


class _DummyScanner:
    async def process(self, text, metadata):
        return {"security_verdict": {"passed": True}, "blocked_reason": ""}


class _DummyAuditor:
    async def process(self, scan_result):
        return {"approved_for_executor": True, "notes": []}


class _DummyScorer:
    def score_and_route(self, text):
        return SimpleNamespace(
            score=0.1,
            selected_tier=1,
            domain="general",
            features={},
            scorer_version="test",
            budget_gated=False,
            selected_provider="gpt-5.4-mini",
        )


class _FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.caption = None
        self.photo = None
        self.video = None
        self.video_note = None
        self.document = None
        self.replies: list[tuple[str, str | None]] = []

    async def reply_text(self, text: str, parse_mode: str | None = None):
        self.replies.append((text, parse_mode))


class _FakeUpdate:
    def __init__(self, text: str, user_id: int = 123):
        self.effective_user = SimpleNamespace(id=user_id)
        self.message = _FakeMessage(text)


class _FakeProviderChain:
    def __init__(self):
        self.providers = [SimpleNamespace(name="gpt-5.4-mini")]
        self.calls = 0

    async def complete(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return CompletionResult(
                content="",
                finish_reason="tool_calls",
                usage=UsageStats(),
                provider="openai_oauth",
                model="gpt-5.4-mini",
                tool_calls=[ToolCall(id="buddy-1", name="buddy_status", arguments={})],
            )
        return CompletionResult(
            content="Buddy is thriving.",
            finish_reason="stop",
            usage=UsageStats(),
            provider="openai_oauth",
            model="gpt-5.4-mini",
        )


@pytest.mark.asyncio
async def test_buddy_tool_dispatches_for_telegram_update(monkeypatch):
    gateway = ABLEGateway(require_telegram=False, skip_phoenix=True)
    try:
        fake_chain = _FakeProviderChain()
        fake_update = _FakeUpdate("How's Groot?")
        tool_calls: list[str] = []
        tenant_calls: list[str] = []

        async def _authorized_tools(registry, client_id=None):
            return registry.get_definitions()

        async def _fake_buddy_status(**kwargs):
            tool_calls.append("buddy_status")
            return "Buddy status snapshot"

        async def _fake_tenant_status(**kwargs):
            tenant_calls.append("tenant_status")
            return "tenant status"

        gateway.scanner = _DummyScanner()
        gateway.auditor = _DummyAuditor()
        gateway.complexity_scorer = _DummyScorer()
        gateway.prompt_enricher = None
        gateway.interaction_logger = None
        gateway.memory = None
        gateway.tracer = None
        gateway.transcript_manager.get_recent_messages = lambda *args, **kwargs: []
        gateway.provider_chain = fake_chain
        gateway.vision_chain = fake_chain
        gateway.tier_chains = {1: fake_chain}
        gateway.tool_registry._tools["buddy_status"].handler = _fake_buddy_status
        gateway.tool_registry._tools["tenant_status"].handler = _fake_tenant_status
        monkeypatch.setattr(gateway_module, "fetch_authorized_tools", _authorized_tools)

        response = await gateway.process_message(
            message="How's Groot?",
            user_id="123",
            client_id="master",
            metadata={"source": "master_telegram", "is_owner": True},
            update=fake_update,
        )

        assert response.startswith("Buddy is thriving.")
        assert "GPT 5.4 Mini [T1]" in response
        assert tool_calls == ["buddy_status"]
        assert tenant_calls == []
        assert fake_update.message.replies
        assert fake_update.message.replies[0][0].startswith("⚙️ [buddy_status]")
        assert "Buddy status snapshot" in fake_update.message.replies[0][0]
    finally:
        await gateway.aclose()
