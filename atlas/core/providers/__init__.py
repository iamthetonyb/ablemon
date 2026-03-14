"""
ATLAS v2 Provider System

Modular LLM provider abstraction with fallback chain.
Supports: Claude Code (subscription), NVIDIA NIM (free), OpenRouter, Anthropic API, Local (Ollama)

Provider chain priority:
1. Claude Code CLI (FREE on subscription — Opus plans, Sonnet delegates)
2. NVIDIA NIM (free tier — Kimi K2.5)
3. OpenRouter (cheap — Qwen 3.5 for research/bulk)
4. Anthropic API (direct — only if no Claude Code sub)
5. Ollama (local — zero cost fallback)
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
from .claude_code import ClaudeCodeProvider

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
    'ClaudeCodeProvider',
]


def create_default_chain(secrets_path: str = None) -> ProviderChain:
    """
    Create default provider chain with Claude Code subscription priority.

    Chain:
    1. Claude Code CLI (FREE on plan — spawns sonnet subagents for execution)
    2. NVIDIA NIM (free tier — Kimi K2.5)
    3. OpenRouter (Qwen 3.5 for research/bulk — $0.60/$3.00 per M)
    4. Anthropic API (direct — $3/$15 Sonnet, $15/$75 Opus)
    5. Ollama (local — qwen3.5 or llama3.2)

    Args:
        secrets_path: Path to secrets directory (defaults to ~/.atlas/.secrets/)
    """
    import os
    from pathlib import Path

    if secrets_path is None:
        secrets_path = Path.home() / '.atlas' / '.secrets'
    else:
        secrets_path = Path(secrets_path)

    def get_secret(name: str) -> str:
        secret_file = secrets_path / name
        if secret_file.exists():
            return secret_file.read_text().strip()
        return os.environ.get(name, '')

    providers = []

    # 1. Claude Code CLI (FREE on subscription)
    # Uses your Claude Code plan — Opus for planning, Sonnet for execution
    # No API key needed — authenticated via CLI login
    providers.append(ClaudeCodeProvider(model="sonnet"))

    # 2. NVIDIA NIM (free tier)
    nvidia_key = get_secret('NVIDIA_API_KEY')
    if nvidia_key:
        providers.append(NVIDIANIMProvider(api_key=nvidia_key))

    # 3. OpenRouter (Qwen 3.5 for research/bulk work — cheap)
    openrouter_key = get_secret('OPENROUTER_API_KEY')
    if openrouter_key:
        providers.append(OpenRouterProvider(api_key=openrouter_key))

    # 4. Anthropic API (direct — only used if Claude Code CLI unavailable)
    anthropic_key = get_secret('ANTHROPIC_API_KEY')
    if anthropic_key:
        providers.append(AnthropicProvider(api_key=anthropic_key))

    # 5. Local Ollama (always available fallback)
    providers.append(OllamaProvider())

    return ProviderChain(providers)
