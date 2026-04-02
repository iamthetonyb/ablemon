"""
ABLE v2 Provider System

PicoClaw-inspired modular LLM provider abstraction with fallback chain.
Supports: NVIDIA NIM (free), OpenRouter, Anthropic, Local (Ollama)
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
from .nvidia_nim import NVIDIANIMProvider
from .openrouter import OpenRouterProvider
from .anthropic_provider import AnthropicProvider
from .ollama import OllamaProvider

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
]


def create_default_chain(secrets_path: str = None) -> ProviderChain:
    """
    Create default provider chain: NVIDIA NIM (free) -> OpenRouter -> Anthropic

    Args:
        secrets_path: Path to secrets directory (defaults to ~/.able/.secrets/)
    """
    import os
    from pathlib import Path

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
