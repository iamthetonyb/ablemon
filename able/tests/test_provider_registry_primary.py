from pathlib import Path

from able.core.routing import provider_registry as provider_registry_module
from able.core.routing.provider_registry import ProviderRegistry


class _AuthenticatedAuthManager:
    def is_authenticated(self, provider_name: str) -> bool:
        return provider_name == "openai_oauth"


def test_gpt_5_4_mini_is_primary_tier_1_provider_when_oauth_is_available(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-nvidia-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setattr(
        provider_registry_module,
        "_auth_manager_instance",
        _AuthenticatedAuthManager(),
    )

    registry = ProviderRegistry.from_yaml(
        Path(__file__).resolve().parents[2] / "config" / "routing_config.yaml"
    )

    primary = registry.get_primary_for_tier(1)
    assert primary is not None
    assert primary.name == "gpt-5.4-mini"
    assert primary.provider_type == "openai_oauth"

    chain = registry.get_fallback_chain(starting_tier=1)
    assert chain
    assert chain[0].name == "gpt-5.4-mini"
