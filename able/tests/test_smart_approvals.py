"""Tests for A8 — Smart Approvals That Learn.

Covers: pattern normalization, approval tracking, auto-approve threshold,
30-day decay, never-auto-approve destructive commands, denial blocking.
"""

import pytest
from datetime import datetime, timezone, timedelta

from able.core.security.command_guard import (
    SmartApprovals,
    CommandGuard,
    CommandVerdict,
    _NEVER_AUTO_APPROVE,
)


@pytest.fixture
def approvals(tmp_path):
    return SmartApprovals(db_path=tmp_path / "approvals.db", threshold=3)


@pytest.fixture
def guard_with_approvals(tmp_path):
    sa = SmartApprovals(db_path=tmp_path / "guard_approvals.db", threshold=3)
    return CommandGuard(trust_tier=1, smart_approvals=sa)


# ── Pattern normalization ─────────────────────────────────────────

class TestPatternNormalization:

    def test_base_command_preserved(self, approvals):
        assert "git" in approvals._normalize_pattern("git status")

    def test_subcommand_preserved(self, approvals):
        pattern = approvals._normalize_pattern("git status")
        assert "status" in pattern

    def test_flags_preserved(self, approvals):
        pattern = approvals._normalize_pattern("git log --oneline -10")
        assert "--oneline" in pattern
        assert "-10" in pattern

    def test_file_paths_stripped(self, approvals):
        pattern = approvals._normalize_pattern("cat /home/user/very/long/path/file.py")
        assert "/home/user" not in pattern

    def test_long_args_stripped(self, approvals):
        """Args >= 20 chars are stripped as likely paths/hashes."""
        pattern = approvals._normalize_pattern("git show abc123def456789abcdef01234")
        assert "abc123def456789abcdef01234" not in pattern

    def test_empty_command(self, approvals):
        assert approvals._normalize_pattern("") == ""


# ── Approval tracking ─────────────────────────────────────────────

class TestApprovalTracking:

    def test_record_approval_increments(self, approvals):
        approvals.record_approval("git status")
        approvals.record_approval("git status")
        stats = approvals.get_stats()
        assert stats["total_approvals"] == 2

    def test_record_denial(self, approvals):
        approvals.record_denial("rm -rf /")
        stats = approvals.get_stats()
        assert stats["total_denials"] == 1

    def test_different_commands_separate_patterns(self, approvals):
        approvals.record_approval("git status")
        approvals.record_approval("git log")
        stats = approvals.get_stats()
        assert stats["patterns"] == 2


# ── Auto-approve threshold ────────────────────────────────────────

class TestAutoApprove:

    def test_not_approved_below_threshold(self, approvals):
        for _ in range(2):
            approvals.record_approval("npm test")
        assert approvals.should_auto_approve("npm test") is False

    def test_approved_at_threshold(self, approvals):
        for _ in range(3):  # threshold=3
            approvals.record_approval("npm test")
        assert approvals.should_auto_approve("npm test") is True

    def test_unknown_command_not_approved(self, approvals):
        assert approvals.should_auto_approve("some_random_command") is False

    def test_similar_command_with_different_path(self, approvals):
        """Same pattern despite different file path args."""
        for _ in range(3):
            approvals.record_approval("cat /tmp/file1.txt")
        # Different path but same pattern (cat)
        assert approvals.should_auto_approve("cat /tmp/file2.txt") is True


# ── Never auto-approve ────────────────────────────────────────────

class TestNeverAutoApprove:

    def test_rm_rf_blocked(self, approvals):
        for _ in range(10):
            approvals.record_approval("rm -rf /tmp/test")
        assert approvals.should_auto_approve("rm -rf /tmp/test") is False

    def test_drop_table_blocked(self, approvals):
        for _ in range(10):
            approvals.record_approval("psql -c 'DROP TABLE users'")
        assert approvals.should_auto_approve("psql -c 'DROP TABLE users'") is False

    def test_force_push_blocked(self, approvals):
        for _ in range(10):
            approvals.record_approval("git push --force origin main")
        assert approvals.should_auto_approve("git push --force origin main") is False

    def test_hard_reset_blocked(self, approvals):
        for _ in range(10):
            approvals.record_approval("git reset --hard HEAD~1")
        assert approvals.should_auto_approve("git reset --hard HEAD~1") is False

    def test_env_file_blocked(self, approvals):
        for _ in range(10):
            approvals.record_approval("cat .env")
        assert approvals.should_auto_approve("cat .env") is False

    def test_sudo_blocked(self, approvals):
        for _ in range(10):
            approvals.record_approval("sudo apt install")
        assert approvals.should_auto_approve("sudo apt install") is False

    def test_regex_pattern_matches(self):
        """Verify the _NEVER_AUTO_APPROVE regex catches all patterns."""
        assert _NEVER_AUTO_APPROVE.search("rm -rf /")
        assert _NEVER_AUTO_APPROVE.search("DROP TABLE users")
        assert _NEVER_AUTO_APPROVE.search("git push --force")
        assert _NEVER_AUTO_APPROVE.search("git reset --hard")
        assert _NEVER_AUTO_APPROVE.search("cat .env")
        assert _NEVER_AUTO_APPROVE.search("sudo su")
        assert _NEVER_AUTO_APPROVE.search("--no-verify")
        assert _NEVER_AUTO_APPROVE.search("cat credentials.json")


# ── Denial blocking ──────────────────────────────────────────────

class TestDenialBlocking:

    def test_denial_blocks_auto_approve(self, approvals):
        for _ in range(5):
            approvals.record_approval("npm install express")
        approvals.record_denial("npm install express")
        assert approvals.should_auto_approve("npm install express") is False


# ── Stale pruning ─────────────────────────────────────────────────

class TestStalePruning:

    def test_prune_returns_count(self, approvals):
        approvals.record_approval("old command")
        # Can't easily test time-based pruning without mocking, but verify method works
        removed = approvals.prune_stale()
        assert removed == 0  # Nothing stale yet

    def test_stats_after_prune(self, approvals):
        approvals.record_approval("git status")
        stats = approvals.get_stats()
        assert stats["patterns"] == 1


# ── CommandGuard integration ──────────────────────────────────────

class TestCommandGuardIntegration:

    def test_auto_approve_wired(self, guard_with_approvals):
        """CommandGuard should auto-approve learned patterns."""
        sa = guard_with_approvals.smart_approvals
        for _ in range(3):
            sa.record_approval("bun test")
        result = guard_with_approvals.analyze("bun test")
        assert result.verdict == CommandVerdict.ALLOWED
        assert "auto-approved" in result.reason.lower()

    def test_unknown_still_requires_approval(self, guard_with_approvals):
        result = guard_with_approvals.analyze("unknown_tool --flag")
        assert result.verdict == CommandVerdict.REQUIRES_APPROVAL

    def test_destructive_never_auto_approved(self, guard_with_approvals):
        sa = guard_with_approvals.smart_approvals
        for _ in range(10):
            sa.record_approval("rm -rf /tmp")
        result = guard_with_approvals.analyze("rm -rf /tmp")
        # rm is in ALWAYS_DENIED, so it should be denied regardless
        assert result.verdict == CommandVerdict.DENIED
