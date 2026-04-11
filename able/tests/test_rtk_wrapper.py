"""Tests for D2 — RTK Token Compression Wrapper."""

import pytest
from able.tools.rtk.wrapper import (
    is_available, should_compress, wrap_command, compress_output, RTKResult,
)
from able.tools.rtk.tracking import RTKTracker, CompressionStats


class TestRTKWrapper:

    def test_is_available(self):
        # RTK may or may not be in PATH depending on shell config
        result = is_available()
        assert isinstance(result, bool)

    def test_should_compress_git(self):
        assert should_compress("git status")
        assert should_compress("git diff --stat")
        assert should_compress("git log --oneline")

    def test_should_compress_ls(self):
        assert should_compress("ls -la")
        assert should_compress("find . -name '*.py'")

    def test_should_not_compress_custom(self):
        assert not should_compress("python3 test.py")
        assert not should_compress("curl https://api.example.com")

    def test_wrap_command_compressible(self):
        wrapped = wrap_command("git status")
        # If RTK available, wraps; otherwise passthrough
        assert "git status" in wrapped or "rtk" in wrapped

    def test_wrap_command_skips_non_compressible(self):
        wrapped = wrap_command("python3 test.py")
        assert wrapped == "python3 test.py"

    def test_compress_output_passthrough(self):
        result = compress_output("python3 test.py", "output text")
        assert not result.rtk_used
        assert result.compressed_output == "output text"
        assert result.savings_pct == 0.0

    def test_result_fields(self):
        result = RTKResult(
            command="git status",
            original_output="long output",
            compressed_output="short",
            savings_pct=0.5,
            rtk_used=True,
            exit_code=0,
        )
        assert result.savings_pct == 0.5
        assert result.rtk_used


class TestRTKTracker:

    def test_record_and_stats(self, tmp_path):
        tracker = RTKTracker(db_path=str(tmp_path / "rtk.db"))
        tracker.record("git status", original_tokens=500, compressed_tokens=50)
        tracker.record("git diff", original_tokens=1000, compressed_tokens=200)
        stats = tracker.get_stats(since_hours=1)
        assert stats.total_commands == 2
        assert stats.total_original_tokens == 1500
        assert stats.total_compressed_tokens == 250
        assert stats.total_savings_pct > 0.8

    def test_empty_stats(self, tmp_path):
        tracker = RTKTracker(db_path=str(tmp_path / "rtk.db"))
        stats = tracker.get_stats()
        assert stats.total_commands == 0

    def test_top_commands(self, tmp_path):
        tracker = RTKTracker(db_path=str(tmp_path / "rtk.db"))
        for _ in range(5):
            tracker.record("git status", 500, 50)
        for _ in range(3):
            tracker.record("git diff", 1000, 300)
        stats = tracker.get_stats()
        assert len(stats.top_commands) == 2
        assert stats.top_commands[0]["command"] == "git status"  # Higher savings
