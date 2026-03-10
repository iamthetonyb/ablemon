"""
ATLAS v3 Gateway Module
Main coordinator for all ATLAS components
"""

# Lazy import to avoid triggering aiohttp/telegram at module load time
# Use: from core.gateway.gateway import ATLASGateway


def get_gateway_class():
    from .gateway import ATLASGateway
    return ATLASGateway


__all__ = ['get_gateway_class']
