"""
ATLAS v2 Core Module
Security-hardened multi-tenant AI agent system

Imports are lazy to allow security module to work without telegram installed.
"""

# Security (always available)
from .security import TrustGate, TrustTier, ThreatLevel, CommandGuard

__all__ = [
    'TrustGate',
    'TrustTier',
    'ThreatLevel',
    'CommandGuard',
]

# Optional imports (require dependencies)
def __getattr__(name):
    if name in ('ScannerAgent', 'AuditorAgent', 'ExecutorAgent', 'AgentContext'):
        from .agents import ScannerAgent, AuditorAgent, ExecutorAgent, AgentContext
        return locals()[name]
    elif name == 'LaneQueue':
        from .queue import LaneQueue
        return LaneQueue
    elif name == 'ATLASGateway':
        from .gateway import ATLASGateway
        return ATLASGateway
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
