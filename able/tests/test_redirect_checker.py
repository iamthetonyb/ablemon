"""Tests for A5 — Enhanced SSRF Guards (RedirectChecker).

Covers: redirect chain validation, blocked hosts, body dropping,
max redirects, cross-domain detection.
"""

import pytest

from able.core.security.egress_inspector import (
    EgressInspector,
    RedirectChecker,
    RedirectResult,
)


@pytest.fixture
def checker():
    return RedirectChecker()


# ── Basic validation ────────────────────────────────────────────

class TestBasicValidation:

    def test_no_redirects(self, checker):
        r = checker.check_redirect_chain("https://api.github.com/test", [])
        assert r.safe
        assert r.hops == 0

    def test_safe_redirect(self, checker):
        r = checker.check_redirect_chain(
            "https://github.com/repo",
            ["https://github.com/repo/tree/main"],
        )
        assert r.safe
        assert r.hops == 1

    def test_multiple_safe_redirects(self, checker):
        r = checker.check_redirect_chain(
            "https://github.com/a",
            ["https://github.com/b", "https://github.com/c"],
        )
        assert r.safe
        assert r.hops == 2
        assert r.final_url == "https://github.com/c"


# ── Blocked hosts ───────────────────────────────────────────────

class TestBlockedHosts:

    def test_initial_url_blocked(self, checker):
        r = checker.check_redirect_chain(
            "http://169.254.169.254/latest/meta-data",
            [],
        )
        assert not r.safe
        assert "Initial URL blocked" in r.reason

    def test_redirect_to_metadata(self, checker):
        r = checker.check_redirect_chain(
            "https://api.github.com/test",
            ["http://169.254.169.254/latest/meta-data"],
        )
        assert not r.safe
        assert r.drop_body
        assert r.blocked_at == "http://169.254.169.254/latest/meta-data"

    def test_redirect_to_google_metadata(self, checker):
        r = checker.check_redirect_chain(
            "https://api.github.com/test",
            ["http://metadata.google.internal/computeMetadata/v1/"],
        )
        assert not r.safe
        assert r.drop_body

    def test_redirect_to_cgnat(self, checker):
        r = checker.check_redirect_chain(
            "https://api.github.com/test",
            ["http://100.100.100.200/metadata"],
        )
        assert not r.safe


# ── Body dropping ───────────────────────────────────────────────

class TestBodyDropping:

    def test_cross_domain_drops_body(self, checker):
        r = checker.check_redirect_chain(
            "https://api.github.com/test",
            ["https://evil-tracker.com/collect"],
        )
        assert r.safe  # Not blocked (not metadata), but...
        assert r.drop_body  # Body dropped for non-allowlisted cross-domain

    def test_same_domain_no_drop(self, checker):
        r = checker.check_redirect_chain(
            "https://github.com/a",
            ["https://github.com/b"],
        )
        assert r.safe
        assert not r.drop_body

    def test_redirect_to_allowlisted_no_drop(self, checker):
        r = checker.check_redirect_chain(
            "https://github.com/a",
            ["https://api.github.com/b"],  # Different host but allowlisted
        )
        assert r.safe
        assert not r.drop_body


# ── Max redirects ───────────────────────────────────────────────

class TestMaxRedirects:

    def test_too_many_redirects(self, checker):
        urls = [f"https://github.com/r{i}" for i in range(11)]
        r = checker.check_redirect_chain("https://github.com/start", urls)
        assert not r.safe
        assert "Too many" in r.reason
        assert r.drop_body

    def test_at_limit_ok(self, checker):
        urls = [f"https://github.com/r{i}" for i in range(10)]
        r = checker.check_redirect_chain("https://github.com/start", urls)
        assert r.safe  # Exactly at limit


# ── Static method compatibility ─────────────────────────────────

class TestStaticMethod:

    def test_validate_redirect_target_safe(self):
        assert EgressInspector.validate_redirect_target("https://github.com/a")

    def test_validate_redirect_target_metadata(self):
        assert not EgressInspector.validate_redirect_target(
            "http://169.254.169.254/meta"
        )

    def test_validate_redirect_target_cgnat(self):
        assert not EgressInspector.validate_redirect_target(
            "http://100.64.1.1/internal"
        )
