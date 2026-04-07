"""
ABLE v2 Tools Module
Secure tool implementations for agent execution.

All tools enforce security through the CommandGuard and TrustGate.
"""

from pathlib import Path

# Lazy imports
def __getattr__(name):
    if name == 'SecureSandbox':
        from .sandbox.executor import SecureSandbox
        return SecureSandbox
    elif name == 'BrowserAutomation':
        from .browser.automation import BrowserAutomation
        return BrowserAutomation
    elif name == 'SecureShell':
        from .shell.secure_shell import SecureShell
        return SecureShell
    elif name == 'UnifiedToolRegistry':
        from .registry import UnifiedToolRegistry
        return UnifiedToolRegistry
    elif name == 'ToolSourceManager':
        from .registry import ToolSourceManager
        return ToolSourceManager
    elif name == 'ToolDefinition':
        from .registry import ToolDefinition
        return ToolDefinition
    elif name == 'ToolResult':
        from .registry import ToolResult
        return ToolResult
    elif name == 'ToolCategory':
        from .registry import ToolCategory
        return ToolCategory
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    'SecureSandbox',
    'BrowserAutomation',
    'SecureShell',
    'UnifiedToolRegistry',
    'ToolSourceManager',
    'ToolDefinition',
    'ToolResult',
    'ToolCategory',
]
