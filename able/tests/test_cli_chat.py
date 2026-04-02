from __future__ import annotations

import asyncio

from able.cli.chat import TerminalApprovalWorkflow, build_parser
from able.core.approval.workflow import ApprovalStatus
from able.core.gateway.gateway import ABLEGateway


def test_chat_parser_defaults():
    parser = build_parser()
    args = parser.parse_args([])

    assert args.session == "local-cli"
    assert args.client == "master"
    assert args.control_port == 8080
    assert args.auto_approve is False


def test_terminal_approval_can_auto_approve():
    workflow = TerminalApprovalWorkflow(auto_approve=True)

    result = asyncio.run(
        workflow.request_approval(
            operation="github_create_pr",
            details={"repo": "iamthetonyb/ABLE"},
            requester_id="local-cli",
            risk_level="high",
        )
    )

    assert result.status == ApprovalStatus.APPROVED
    assert "automatically" in (result.reason or "")


def test_resolve_channel_prefers_cli_metadata():
    assert ABLEGateway._resolve_channel(None, {"channel": "cli"}) == "cli"
    assert ABLEGateway._resolve_channel(None, {"source": "cli"}) == "cli"
    assert ABLEGateway._resolve_channel(None, None) == "api"


def test_chat_parser_no_stream_flag():
    parser = build_parser()
    args = parser.parse_args(["--no-stream"])
    assert args.no_stream is True

    args = parser.parse_args([])
    assert args.no_stream is False


def test_gateway_has_stream_message():
    """Verify the gateway exposes a stream_message async generator."""
    assert hasattr(ABLEGateway, "stream_message")
    import inspect
    assert inspect.isasyncgenfunction(ABLEGateway.stream_message)
