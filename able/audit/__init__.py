"""
ABLE v2 Audit Module
Comprehensive audit logging, alerting, and tracing.
"""

from pathlib import Path

# Lazy imports
def __getattr__(name):
    if name == 'AlertManager':
        from .alerts.alert_manager import AlertManager
        return AlertManager
    elif name == 'TraceLogger':
        from .traces.trace_logger import TraceLogger
        return TraceLogger
    elif name == 'AuditLog':
        from .logs.audit_log import AuditLog
        return AuditLog
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    'AlertManager',
    'TraceLogger',
    'AuditLog',
]
