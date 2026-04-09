"""
ABLE v2 Provider System

PicoClaw-inspired modular LLM provider abstraction with fallback chain.
Supports: NVIDIA NIM (free), OpenRouter, Anthropic, Local (Ollama)

Provider-specific classes are lazy-loaded on first access to avoid
importing heavy SDKs (~250ms for anthropic alone) during gateway startup.
Import directly when needed: ``from able.core.providers.anthropic_provider import AnthropicProvider``
"""

from .base import (
    LLMProvider,
    ProviderChain,
    CompletionResult,
    ProviderError,
    AllProvidersFailedError,
    Message,
    ToolCall,
)

__all__ = [
    'LLMProvider',
    'ProviderChain',
    'CompletionResult',
    'ProviderError',
    'AllProvidersFailedError',
    'Message',
    'ToolCall',
    'NVIDIANIMProvider',
    'OpenRouterProvider',
    'AnthropicProvider',
    'OllamaProvider',
    'ManagedAgentProvider',
]

# Lazy-load provider classes — SDK imports are deferred to first access
_LAZY_PROVIDERS = {
    'NVIDIANIMProvider': ('.nvidia_nim', 'NVIDIANIMProvider'),
    'OpenRouterProvider': ('.openrouter', 'OpenRouterProvider'),
    'AnthropicProvider': ('.anthropic_provider', 'AnthropicProvider'),
    'OllamaProvider': ('.ollama', 'OllamaProvider'),
    'ManagedAgentProvider': ('.managed_agent_provider', 'ManagedAgentProvider'),
}


def __getattr__(name: str):
    if name in _LAZY_PROVIDERS:
        module_path, attr = _LAZY_PROVIDERS[name]
        import importlib
        mod = importlib.import_module(module_path, __package__)
        cls = getattr(mod, attr)
        globals()[name] = cls  # Cache for subsequent access
        return cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def create_default_chain(secrets_path: str = None) -> ProviderChain:
    """
    Create default provider chain: NVIDIA NIM (free) -> OpenRouter -> Anthropic

    Args:
        secrets_path: Path to secrets directory (defaults to ~/.able/.secrets/)
    """
    import os
    from pathlib import Path
    # Explicit imports — __getattr__ only resolves external attribute access,
    # not bare names inside the same module.
    from .nvidia_nim import NVIDIANIMProvider
    from .openrouter import OpenRouterProvider
    from .anthropic_provider import AnthropicProvider
    from .ollama import OllamaProvider

    if secrets_path is None:
        secrets_path = Path.home() / '.able' / '.secrets'
    else:
        secrets_path = Path(secrets_path)

    def get_secret(name: str) -> str:
        secret_file = secrets_path / name
        if secret_file.exists():
            return secret_file.read_text().strip()
        return os.environ.get(name, '')

    providers = []

    # 1. NVIDIA NIM (free tier)
    nvidia_key = get_secret('NVIDIA_API_KEY')
    if nvidia_key:
        providers.append(NVIDIANIMProvider(api_key=nvidia_key))

    # 2. OpenRouter (fallback)
    openrouter_key = get_secret('OPENROUTER_API_KEY')
    if openrouter_key:
        providers.append(OpenRouterProvider(api_key=openrouter_key))

    # 3. Anthropic (premium)
    anthropic_key = get_secret('ANTHROPIC_API_KEY')
    if anthropic_key:
        providers.append(AnthropicProvider(api_key=anthropic_key))

    # 4. Local Ollama (always available fallback)
    providers.append(OllamaProvider())

    return ProviderChain(providers)
