"""
ABLE v3 Gateway Module
Main coordinator for all ABLE components
"""

# Lazy import to avoid triggering aiohttp/telegram at module load time
# Use: from core.gateway.gateway import ABLEGateway


def get_gateway_class():
    from .gateway import ABLEGateway
    return ABLEGateway


__all__ = ['get_gateway_class']
