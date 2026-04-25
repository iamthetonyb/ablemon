from types import SimpleNamespace

import pytest

from able.core.gateway.gateway import ABLEGateway
from able.core.gateway.initiative import InitiativeEngine


def test_cron_leader_gate_defaults_to_follower():
    assert ABLEGateway._cron_enabled_from_env({}) is False
    assert ABLEGateway._cron_enabled_from_env({"ABLE_CRON_ENABLED": "0"}) is False
    assert ABLEGateway._cron_enabled_from_env({"ABLE_CRON_ENABLED": "false"}) is False
    assert ABLEGateway._cron_enabled_from_env({"ABLE_CRON_ROLE": "follower"}) is False


def test_cron_leader_gate_allows_explicit_leader():
    assert ABLEGateway._cron_enabled_from_env({"ABLE_CRON_ENABLED": "1"}) is True
    assert ABLEGateway._cron_enabled_from_env({"ABLE_CRON_ENABLED": "true"}) is True
    assert ABLEGateway._cron_enabled_from_env({"ABLE_CRON_ROLE": "leader"}) is True


def test_telegram_polling_defaults_to_cron_leader_only():
    assert ABLEGateway._telegram_polling_enabled_from_env({}) is False
    assert ABLEGateway._telegram_polling_enabled_from_env({"ABLE_CRON_ENABLED": "1"}) is True
    assert ABLEGateway._telegram_polling_enabled_from_env(
        {"ABLE_CRON_ENABLED": "1", "ABLE_TELEGRAM_ENABLED": "0"}
    ) is False
    assert ABLEGateway._telegram_polling_enabled_from_env({"ABLE_TELEGRAM_ENABLED": "1"}) is True


@pytest.mark.asyncio
async def test_github_digest_missing_token_logs_without_telegram(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    gateway = SimpleNamespace(
        audit_dir=tmp_path,
        master_bot=SimpleNamespace(),
        owner_telegram_id="123",
        github=SimpleNamespace(list_repos=lambda: []),
    )
    engine = InitiativeEngine(gateway)
    sent = []

    async def fake_send(message: str, job_name: str = "unknown"):
        sent.append((job_name, message))

    engine._send_to_owner = fake_send

    await engine._github_digest()

    assert sent == []
