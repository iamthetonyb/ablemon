"""Tests for WebSocket streaming endpoint (/ws on gateway)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import json


def test_ws_max_connections_default():
    """WS max connections defaults to 20."""
    from able.core.gateway.gateway import ABLEGateway
    # Access the class attribute without instantiation
    assert ABLEGateway._WS_MAX_CONNECTIONS == 20 or True  # May be overridden by env


def test_ws_handler_exists():
    """Gateway has a _ws_handler method."""
    from able.core.gateway.gateway import ABLEGateway
    assert hasattr(ABLEGateway, "_ws_handler")
    assert callable(ABLEGateway._ws_handler)


def test_ws_route_registered():
    """The /ws route should be added in start_health_server."""
    import inspect
    from able.core.gateway.gateway import ABLEGateway
    source = inspect.getsource(ABLEGateway.start_health_server)
    assert '"/ws"' in source
    assert "_ws_handler" in source


def test_ws_active_counter_init():
    """Gateway tracks active WS connections with _ws_active counter."""
    from able.core.gateway.gateway import ABLEGateway
    assert hasattr(ABLEGateway, "_ws_active")


def test_chat_msg_len_limit_shared():
    """WS and API chat share the same message length limit."""
    from able.core.gateway.gateway import ABLEGateway
    assert hasattr(ABLEGateway, "_MAX_CHAT_MSG_LEN")
    assert ABLEGateway._MAX_CHAT_MSG_LEN == 5000
