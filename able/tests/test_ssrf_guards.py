"""Tests for A5 — Enhanced SSRF Guards (post-redirect validation, body dropping)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

from able.core.security.egress_inspector import (
    EgressInspector,
    EgressRisk,
)


# ── validate_redirect_target ──────────────────────────────────────

class TestValidateRedirectTarget:
    """Test the static redirect validation method."""

    def test_safe_url_passes(self):
        assert EgressInspector.validate_redirect_target("https://example.com/page") is True

    def test_cloud_metadata_ip_blocked(self):
        assert EgressInspector.validate_redirect_target("http://169.254.169.254/latest/meta-data") is False

    def test_cloud_metadata_hostname_blocked(self):
        assert EgressInspector.validate_redirect_target("http://metadata.google.internal/computeMetadata/v1/") is False

    def test_metadata_goog_blocked(self):
        assert EgressInspector.validate_redirect_target("http://metadata.goog/computeMetadata/v1/") is False

    def test_cgnat_range_blocked(self):
        assert EgressInspector.validate_redirect_target("http://100.100.100.200/") is False

    def test_loopback_not_in_safelist_blocked(self):
        # 127.0.0.2 is loopback but not in default safe_hosts
        assert EgressInspector.validate_redirect_target("http://127.0.0.2/") is False

    def test_link_local_blocked(self):
        assert EgressInspector.validate_redirect_target("http://169.254.1.1/") is False

    def test_safe_host_loopback_allowed(self):
        # 127.0.0.1 IS in the safe hosts list
        assert EgressInspector.validate_redirect_target(
            "http://127.0.0.1:8080/",
            safe_hosts=frozenset({"127.0.0.1"}),
        ) is True

    def test_normal_hostname_passes(self):
        assert EgressInspector.validate_redirect_target("https://docs.python.org/3/") is True

    def test_aws_ecs_metadata_blocked(self):
        assert EgressInspector.validate_redirect_target("http://169.254.170.2/v2/metadata") is False

    def test_alibaba_metadata_blocked(self):
        assert EgressInspector.validate_redirect_target("http://100.100.100.200/latest/meta-data") is False


# ── EgressInspector.inspect — cloud metadata detection ────────────

class TestEgressInspectorMetadata:
    """Test that metadata endpoints are caught as CRITICAL."""

    def test_curl_to_metadata_ip(self):
        inspector = EgressInspector()
        verdict = inspector.inspect("curl http://169.254.169.254/latest/meta-data/iam/security-credentials/")
        assert verdict.risk_level == EgressRisk.CRITICAL
        assert verdict.requires_approval is True

    def test_curl_to_metadata_hostname(self):
        inspector = EgressInspector()
        verdict = inspector.inspect("curl http://metadata.google.internal/computeMetadata/v1/")
        assert verdict.risk_level == EgressRisk.CRITICAL

    def test_wget_to_metadata_goog(self):
        inspector = EgressInspector()
        verdict = inspector.inspect("wget http://metadata.goog/computeMetadata/v1/project/project-id")
        assert verdict.risk_level == EgressRisk.CRITICAL

    def test_cgnat_range_flagged(self):
        inspector = EgressInspector()
        verdict = inspector.inspect("curl http://100.100.100.200/")
        high_or_above = [d for d in verdict.destinations if d.risk in (EgressRisk.HIGH, EgressRisk.CRITICAL)]
        assert len(high_or_above) > 0


# ── handle_web_fetch SSRF protection ──────────────────────────────

class TestWebFetchSSRF:
    """Test that handle_web_fetch blocks SSRF redirect chains."""

    @pytest.fixture
    def mock_ctx(self):
        ctx = MagicMock()
        ctx.metadata = {"web_search": MagicMock()}
        return ctx

    def test_direct_metadata_blocked(self, mock_ctx):
        from able.core.gateway.tool_defs.web_tools import handle_web_fetch
        result = asyncio.run(
            handle_web_fetch({"url": "http://169.254.169.254/latest/meta-data/"}, mock_ctx)
        )
        assert "Blocked" in result
        assert "restricted" in result.lower()

    def test_direct_metadata_hostname_blocked(self, mock_ctx):
        from able.core.gateway.tool_defs.web_tools import handle_web_fetch
        result = asyncio.run(
            handle_web_fetch({"url": "http://metadata.google.internal/"}, mock_ctx)
        )
        assert "Blocked" in result

    def test_no_url_returns_warning(self, mock_ctx):
        from able.core.gateway.tool_defs.web_tools import handle_web_fetch
        result = asyncio.run(
            handle_web_fetch({"url": ""}, mock_ctx)
        )
        assert "No URL" in result

    def test_cgnat_blocked(self, mock_ctx):
        from able.core.gateway.tool_defs.web_tools import handle_web_fetch
        result = asyncio.run(
            handle_web_fetch({"url": "http://100.100.100.200/"}, mock_ctx)
        )
        assert "Blocked" in result
