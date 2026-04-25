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


def test_telegram_mode_prefers_webhook_when_url_configured():
    env = {
        "ABLE_TELEGRAM_ENABLED": "1",
        "ABLE_TELEGRAM_WEBHOOK_URL": "https://able.example.com",
    }
    assert ABLEGateway._telegram_mode_from_env(env) == "webhook"
    assert ABLEGateway._telegram_polling_enabled_from_env(env) is False
    assert (
        ABLEGateway._normalize_telegram_webhook_url("https://able.example.com")
        == "https://able.example.com/webhook/telegram"
    )


def test_telegram_mode_explicit_webhook_requires_url():
    assert ABLEGateway._telegram_mode_from_env({"ABLE_TELEGRAM_MODE": "webhook"}) == "off"
    assert ABLEGateway._telegram_mode_from_env({
        "ABLE_TELEGRAM_MODE": "webhook",
        "ABLE_TELEGRAM_WEBHOOK_URL": "https://able.example.com/webhook/telegram",
    }) == "webhook"
    assert ABLEGateway._telegram_mode_from_env({
        "ABLE_TELEGRAM_MODE": "polling",
        "ABLE_TELEGRAM_WEBHOOK_URL": "https://able.example.com/webhook/telegram",
    }) == "polling"


def test_telegram_webhook_secret_accepts_header_or_path():
    gateway = object.__new__(ABLEGateway)
    gateway.telegram_webhook_secret = "secret-123"

    header_request = SimpleNamespace(
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret-123"},
        match_info={},
    )
    path_request = SimpleNamespace(headers={}, match_info={"secret": "secret-123"})
    bad_request = SimpleNamespace(
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        match_info={"secret": "wrong"},
    )

    assert gateway._verify_telegram_webhook_request(header_request) is True
    assert gateway._verify_telegram_webhook_request(path_request) is True
    assert gateway._verify_telegram_webhook_request(bad_request) is False


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
