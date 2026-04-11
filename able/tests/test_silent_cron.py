"""Tests for F9 — Silent Cron Responses.

Covers: explicit [SILENT] marker, empty output, trivial patterns,
non-silent passthrough, stats tracking, suppress_trivial toggle.
"""

import pytest

from able.scheduler.silent_cron import (
    SilentCheckResult,
    SilentFilter,
    SilentStats,
)


@pytest.fixture
def filt():
    return SilentFilter()


@pytest.fixture
def no_trivial():
    return SilentFilter(suppress_trivial=False)


# ── Explicit [SILENT] marker ────────────────────────────────────

class TestExplicitSilent:

    def test_silent_marker(self, filt):
        r = filt.check("[SILENT] Nothing new")
        assert r.silent
        assert r.reason == "explicit_marker"
        assert r.stripped_output == "Nothing new"

    def test_silent_marker_lowercase(self, filt):
        r = filt.check("[silent] no changes")
        assert r.silent
        assert r.reason == "explicit_marker"

    def test_silent_marker_with_whitespace(self, filt):
        r = filt.check("  [SILENT] padded")
        assert r.silent

    def test_silent_marker_only(self, filt):
        r = filt.check("[SILENT]")
        assert r.silent
        assert r.stripped_output == ""

    def test_silent_marker_mid_string_not_matched(self, filt):
        r = filt.check("Some output [SILENT] here")
        assert not r.silent  # Only matches at start


# ── Empty output ────────────────────────────────────────────────

class TestEmptyOutput:

    def test_none(self, filt):
        r = filt.check(None)
        assert r.silent
        assert r.reason == "empty"

    def test_empty_string(self, filt):
        r = filt.check("")
        assert r.silent
        assert r.reason == "empty"

    def test_whitespace_only(self, filt):
        r = filt.check("   \n\t  ")
        assert r.silent
        assert r.reason == "empty"


# ── Trivial patterns ───────────────────────────────────────────

class TestTrivialPatterns:

    def test_ok(self, filt):
        r = filt.check("ok")
        assert r.silent
        assert r.reason == "trivial"

    def test_done(self, filt):
        r = filt.check("Done.")
        assert r.silent
        assert r.reason == "trivial"

    def test_no_changes(self, filt):
        r = filt.check("No changes")
        assert r.silent
        assert r.reason == "trivial"

    def test_no_updates(self, filt):
        r = filt.check("No updates")
        assert r.silent
        assert r.reason == "trivial"

    def test_nothing_to_report(self, filt):
        r = filt.check("Nothing to report")
        assert r.silent
        assert r.reason == "trivial"

    def test_nothing_to_do(self, filt):
        r = filt.check("Nothing to do")
        assert r.silent

    def test_trivial_disabled(self, no_trivial):
        r = no_trivial.check("ok")
        assert not r.silent  # Trivial suppression disabled


# ── Non-silent passthrough ──────────────────────────────────────

class TestNonSilent:

    def test_meaningful_output(self, filt):
        r = filt.check("Found 3 new insights about user behavior")
        assert not r.silent
        assert r.reason == ""
        assert r.stripped_output == "Found 3 new insights about user behavior"

    def test_error_message(self, filt):
        r = filt.check("Error: database connection failed")
        assert not r.silent

    def test_multiline_output(self, filt):
        r = filt.check("Line 1\nLine 2\nLine 3")
        assert not r.silent

    def test_ok_with_more(self, filt):
        r = filt.check("ok, processed 5 items")
        assert not r.silent  # Not just "ok"


# ── Stats tracking ──────────────────────────────────────────────

class TestStats:

    def test_initial_stats(self, filt):
        s = filt.stats
        assert s.total_checked == 0
        assert s.suppressed == 0
        assert s.delivered == 0
        assert s.suppression_rate == 0.0

    def test_stats_after_checks(self, filt):
        filt.check("[SILENT] noop")
        filt.check("")
        filt.check("ok")
        filt.check("Important finding!")
        s = filt.stats
        assert s.total_checked == 4
        assert s.suppressed == 3
        assert s.delivered == 1
        assert s.suppression_rate == pytest.approx(0.75)

    def test_stats_by_reason(self, filt):
        filt.check("[SILENT] x")
        filt.check("")
        filt.check("Done.")
        s = filt.stats
        assert s.by_reason["explicit_marker"] == 1
        assert s.by_reason["empty"] == 1
        assert s.by_reason["trivial"] == 1

    def test_reset_stats(self, filt):
        filt.check("[SILENT] x")
        filt.check("real output")
        filt.reset_stats()
        s = filt.stats
        assert s.total_checked == 0
        assert s.suppressed == 0
        assert s.delivered == 0
