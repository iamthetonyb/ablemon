"""
Provider Registry — Configurable multi-tier provider management.

Replaces hardcoded provider fallback chains with a YAML-driven registry
that maps provider tiers to specific models, costs, and capabilities.

Usage:
    registry = ProviderRegistry.from_yaml("config/routing_config.yaml")
    provider = registry.get_provider(tier=1)
    chain = registry.build_chain_for_tier(tier=2)
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class ProviderTierConfig:
    """Configuration for a single provider in the registry."""
    name: str
    tier: int                    # 1=default, 2=escalation, 3=background, 4=premium
    provider_type: str           # nvidia_nim, openrouter, anthropic, ollama
    endpoint: str                # API endpoint or base URL
    model_id: str                # Model string for the API
    cost_per_m_input: float      # $ per million input tokens
    cost_per_m_output: float     # $ per million output tokens
    max_context: int             # Token limit
    supports_tools: bool = True
    supports_vision: bool = False
    throughput_tps: int = 50     # Tokens per second benchmark
    enabled: bool = True
    fallback_to: Optional[str] = None  # Name of fallback provider
    api_key_env: str = ""        # Env var name for API key
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def api_key(self) -> Optional[str]:
        """Resolve API key from environment."""
        if not self.api_key_env:
            return None
        return os.environ.get(self.api_key_env)

    @property
    def is_available(self) -> bool:
        """Check if this provider can be used (key present and enabled)."""
        if not self.enabled:
            return False
        # Ollama doesn't need a key
        if self.provider_type == "ollama":
            return True
        # OAuth providers check auth.json, not env vars
        if self.provider_type == "openai_oauth":
            try:
                from able.core.auth.manager import AuthManager
                return AuthManager().is_authenticated("openai_oauth")
            except Exception:
                return False
        # Claude Code uses CLI + Max subscription — check CLI exists
        if self.provider_type == "claude_code":
            import shutil
            return shutil.which("claude") is not None
        return self.api_key is not None


class ProviderRegistry:
    """
    Registry of all configured providers, loaded from YAML.

    Provides tier-based access, fallback chain construction,
    and cost lookups for the billing system.
    """

    def __init__(self, providers: List[ProviderTierConfig]):
        self._providers: Dict[str, ProviderTierConfig] = {}
        self._by_tier: Dict[int, List[ProviderTierConfig]] = {}

        for p in providers:
            self._providers[p.name] = p
            self._by_tier.setdefault(p.tier, []).append(p)

    @classmethod
    def from_yaml(cls, config_path: str | Path) -> "ProviderRegistry":
        """Load registry from a YAML config file."""
        path = Path(config_path)
        if not path.exists():
            logger.warning(f"Routing config not found at {path}, using empty registry")
            return cls([])

        with open(path) as f:
            data = yaml.safe_load(f)

        providers = []
        for entry in data.get("providers", []):
            try:
                config = ProviderTierConfig(
                    name=entry["name"],
                    tier=entry["tier"],
                    provider_type=entry["provider_type"],
                    endpoint=entry.get("endpoint", ""),
                    model_id=entry["model_id"],
                    cost_per_m_input=float(entry.get("cost_per_m_input", 0.0)),
                    cost_per_m_output=float(entry.get("cost_per_m_output", 0.0)),
                    max_context=int(entry.get("max_context", 128000)),
                    supports_tools=entry.get("supports_tools", True),
                    supports_vision=entry.get("supports_vision", False),
                    throughput_tps=int(entry.get("throughput_tps", 50)),
                    enabled=entry.get("enabled", True),
                    fallback_to=entry.get("fallback_to"),
                    api_key_env=entry.get("api_key_env", ""),
                    extra=entry.get("extra", {}),
                )
                providers.append(config)
            except (KeyError, ValueError) as e:
                logger.error(f"Invalid provider config entry: {e} — skipping")

        logger.info(f"Loaded {len(providers)} providers from {path}")
        return cls(providers)

    def get_provider_config(self, name: str) -> Optional[ProviderTierConfig]:
        """Get a specific provider config by name."""
        return self._providers.get(name)

    def get_providers_for_tier(self, tier: int) -> List[ProviderTierConfig]:
        """Get all providers configured for a specific tier."""
        return [p for p in self._by_tier.get(tier, []) if p.is_available]

    def get_primary_for_tier(self, tier: int) -> Optional[ProviderTierConfig]:
        """Get the first available provider for a tier."""
        available = self.get_providers_for_tier(tier)
        return available[0] if available else None

    def get_fallback_chain(self, starting_tier: int) -> List[ProviderTierConfig]:
        """
        Build a fallback chain starting from a tier.

        Follows fallback_to links, then falls through to higher-numbered tiers.
        Returns an ordered list of providers to try.
        """
        chain: List[ProviderTierConfig] = []
        seen: set = set()

        # First: add providers for the requested tier
        for p in self.get_providers_for_tier(starting_tier):
            if p.name not in seen:
                chain.append(p)
                seen.add(p.name)

        # Then: follow explicit fallback links
        current = chain[-1] if chain else None
        while current and current.fallback_to:
            fallback = self._providers.get(current.fallback_to)
            if fallback and fallback.name not in seen and fallback.is_available:
                chain.append(fallback)
                seen.add(fallback.name)
                current = fallback
            else:
                break

        # Finally: add remaining tiers in order (skip tier 3 — background only)
        for tier in sorted(self._by_tier.keys()):
            if tier == 3:
                continue  # M2.7 is never for user-facing requests
            for p in self._by_tier[tier]:
                if p.name not in seen and p.is_available:
                    chain.append(p)
                    seen.add(p.name)

        return chain

    def get_cost(self, provider_name: str) -> Dict[str, float]:
        """Get cost rates for a provider (for billing integration)."""
        p = self._providers.get(provider_name)
        if not p:
            return {"input": 0.0, "output": 0.0}
        return {"input": p.cost_per_m_input, "output": p.cost_per_m_output}

    def get_all_costs(self) -> Dict[str, Dict[str, float]]:
        """Get cost map for all providers (for billing tracker)."""
        return {name: self.get_cost(name) for name in self._providers}

    @property
    def all_providers(self) -> List[ProviderTierConfig]:
        """All registered provider configs."""
        return list(self._providers.values())

    @property
    def available_providers(self) -> List[ProviderTierConfig]:
        """All available (enabled + keyed) provider configs."""
        return [p for p in self._providers.values() if p.is_available]

    @property
    def tiers(self) -> List[int]:
        """All configured tier numbers."""
        return sorted(self._by_tier.keys())

    def build_llm_providers(self) -> List[Any]:
        """
        Instantiate actual LLMProvider objects from the registry configs.

        Returns a list of LLMProvider instances ordered by tier, skipping
        tier 3 (background-only) providers. Providers whose API keys are
        missing are silently skipped.
        """
        from able.core.providers.nvidia_nim import NVIDIANIMProvider
        from able.core.providers.openrouter import OpenRouterProvider
        from able.core.providers.anthropic_provider import AnthropicProvider
        from able.core.providers.ollama import OllamaProvider
        from able.core.providers.base import ProviderConfig

        providers = []

        for tier_config in self.get_fallback_chain(starting_tier=1):
            try:
                provider = self._instantiate_provider(tier_config)
                if provider:
                    providers.append(provider)
                    logger.info(
                        f"Provider added: {tier_config.name} "
                        f"(tier {tier_config.tier}, {tier_config.model_id})"
                    )
            except Exception as e:
                logger.warning(f"Failed to init {tier_config.name}: {e}")

        return providers

    def _instantiate_provider(self, config: ProviderTierConfig) -> Optional[Any]:
        """Create an LLMProvider instance from a tier config."""
        from able.core.providers.nvidia_nim import NVIDIANIMProvider
        from able.core.providers.openrouter import OpenRouterProvider
        from able.core.providers.anthropic_provider import AnthropicProvider
        from able.core.providers.ollama import OllamaProvider
        from able.core.providers.base import ProviderConfig

        key = config.api_key
        ptype = config.provider_type

        if ptype == "nvidia_nim":
            if not key:
                return None
            return NVIDIANIMProvider(api_key=key, model=config.model_id)

        elif ptype == "openrouter":
            if not key:
                return None
            return OpenRouterProvider(
                api_key=key,
                model=config.model_id,
                base_url=config.endpoint if config.endpoint else None,
                timeout=config.extra.get("timeout", 600.0),
            )

        elif ptype == "anthropic":
            if not key:
                return None
            extended_thinking = config.extra.get("extended_thinking", False)
            thinking_budget = config.extra.get("thinking_budget_tokens", 16000)
            return AnthropicProvider(
                api_key=key,
                model=config.model_id,
                extended_thinking=extended_thinking,
                thinking_budget_tokens=thinking_budget,
            )

        elif ptype == "ollama":
            return OllamaProvider(
                model=config.model_id,
                base_url=config.endpoint or "http://localhost:11434",
            )

        elif ptype == "claude_code":
            try:
                from able.core.providers.claude_code_provider import ClaudeCodeProvider
                return ClaudeCodeProvider(model=config.model_id)
            except ImportError as e:
                logger.warning(f"Claude Code provider deps missing for {config.name}: {e}")
                return None

        elif ptype == "openai_oauth":
            try:
                from able.core.providers.openai_oauth import OpenAIChatGPTProvider
                from able.core.auth.manager import AuthManager
                auth_mgr = AuthManager()
                if not auth_mgr.is_authenticated("openai_oauth"):
                    logger.warning(f"OpenAI OAuth not authenticated for {config.name}")
                    return None
                reasoning_effort = config.extra.get("reasoning_effort", "none")
                return OpenAIChatGPTProvider(
                    config=ProviderConfig(model=config.model_id),
                    auth_manager=auth_mgr,
                    reasoning_effort=reasoning_effort,
                )
            except ImportError as e:
                logger.warning(f"OpenAI OAuth deps missing for {config.name}: {e}")
                return None

        else:
            logger.warning(f"Unknown provider type: {ptype}")
            return None

    def build_provider_chain(self) -> Any:
        """Build a ProviderChain from the registry (full fallback chain)."""
        from able.core.providers.base import ProviderChain

        llm_providers = self.build_llm_providers()
        if not llm_providers:
            logger.error("No AI providers available — ABLE will not respond!")
        return ProviderChain(llm_providers)

    def build_chain_for_tier(self, tier: int) -> Any:
        """Build a ProviderChain starting from a specific tier."""
        from able.core.providers.base import ProviderChain

        chain_configs = self.get_fallback_chain(starting_tier=tier)
        providers = []
        for tc in chain_configs:
            try:
                p = self._instantiate_provider(tc)
                if p:
                    providers.append(p)
            except Exception as e:
                logger.warning(f"Failed to init {tc.name} for tier {tier} chain: {e}")

        return ProviderChain(providers)
