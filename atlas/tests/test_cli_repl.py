"""Tests for ATLAS CLI REPL — covers config, slash commands, logging, trust scan, failure cap."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.cli.repl import ATLASRepl, REPLConfig
from atlas.cli.renderer import CLIRenderer
from atlas.cli.history import SessionHistory


# ── Helpers ───────────────────────────────────────────────────────


def _make_repl(tmp_path: Path, **overrides) -> ATLASRepl:
    """Build a REPL with session_dir pointed at tmp_path, components stubbed."""
    config = REPLConfig(session_dir=tmp_path, **overrides)
    repl = ATLASRepl(config=config)
    # Stub renderer to avoid terminal I/O in tests
    repl._renderer = CLIRenderer()
    repl._initialized = True
    return repl


# ── REPLConfig defaults ──────────────────────────────────────────


class TestREPLConfig:
    def test_defaults(self):
        cfg = REPLConfig()
        assert cfg.model_tier is None
        assert cfg.tenant_id == "tony"
        assert cfg.offline is False
        assert cfg.safe_mode is True
        assert cfg.max_tool_failures == 3

    def test_session_dir_default(self):
        cfg = REPLConfig()
        assert cfg.session_dir == Path.home() / ".atlas" / "sessions"

    def test_override(self):
        cfg = REPLConfig(model_tier=2, offline=True, tenant_id="test")
        assert cfg.model_tier == 2
        assert cfg.offline is True
        assert cfg.tenant_id == "test"


# ── Slash commands ────────────────────────────────────────────────


class TestSlashCommands:
    @pytest.fixture
    def repl(self, tmp_path):
        return _make_repl(tmp_path)

    @pytest.mark.asyncio
    async def test_exit(self, repl):
        assert await repl._handle_slash("/exit") == "EXIT"
        assert await repl._handle_slash("/quit") == "EXIT"
        assert await repl._handle_slash("/q") == "EXIT"

    @pytest.mark.asyncio
    async def test_model(self, repl):
        result = await repl._handle_slash("/model")
        assert "auto" in result

    @pytest.mark.asyncio
    async def test_model_with_tier(self, repl):
        repl.config.model_tier = 4
        result = await repl._handle_slash("/model")
        assert "4" in result

    @pytest.mark.asyncio
    async def test_tier_switch(self, repl):
        result = await repl._handle_slash("/tier 2")
        assert "2" in result
        assert repl.config.model_tier == 2

    @pytest.mark.asyncio
    async def test_tier_invalid(self, repl):
        result = await repl._handle_slash("/tier abc")
        assert "Invalid" in result

    @pytest.mark.asyncio
    async def test_offline_toggle(self, repl):
        assert repl.config.offline is False
        result = await repl._handle_slash("/offline")
        assert "ON" in result
        assert repl.config.offline is True
        result = await repl._handle_slash("/offline")
        assert "OFF" in result
        assert repl.config.offline is False

    @pytest.mark.asyncio
    async def test_cost(self, repl):
        repl.messages = [{"role": "user", "content": "hi"}]
        result = await repl._handle_slash("/cost")
        assert "1 messages" in result

    @pytest.mark.asyncio
    async def test_compact_no_op(self, repl):
        repl.messages = [{"role": "user", "content": "hi"}]
        result = await repl._handle_slash("/compact")
        assert "Nothing" in result

    @pytest.mark.asyncio
    async def test_compact_trims(self, repl):
        repl.messages = [{"role": "user", "content": f"msg{i}"} for i in range(15)]
        result = await repl._handle_slash("/compact")
        assert "15" in result
        assert len(repl.messages) == 7  # 1 kept + 6 tail

    @pytest.mark.asyncio
    async def test_unknown_command(self, repl):
        result = await repl._handle_slash("/foobar")
        assert "Unknown" in result
        assert "/exit" in result

    @pytest.mark.asyncio
    async def test_history_empty(self, repl):
        result = await repl._handle_slash("/history")
        assert "No previous sessions" in result


# ── Session logging ───────────────────────────────────────────────


class TestSessionLogging:
    def test_log_turn_creates_jsonl(self, tmp_path):
        repl = _make_repl(tmp_path)
        repl._log_turn("hello", "world", tier=1, score=0.2)

        assert repl.session_log_path.exists()
        lines = repl.session_log_path.read_text().strip().splitlines()
        assert len(lines) == 1

        data = json.loads(lines[0])
        assert data["user_input"] == "hello"
        assert data["response"] == "world"
        assert data["tier"] == 1
        assert data["complexity_score"] == 0.2
        assert data["session_id"] == repl.session_id

    def test_log_turn_appends(self, tmp_path):
        repl = _make_repl(tmp_path)
        repl._log_turn("a", "b", 1, 0.1)
        repl._log_turn("c", "d", 2, 0.5)

        lines = repl.session_log_path.read_text().strip().splitlines()
        assert len(lines) == 2


# ── SessionHistory ────────────────────────────────────────────────


class TestSessionHistory:
    def test_list_sessions(self, tmp_path):
        (tmp_path / "abc123.jsonl").write_text('{"user_input":"hi"}\n')
        (tmp_path / "def456.jsonl").write_text('{"user_input":"yo"}\n')
        sh = SessionHistory(tmp_path)
        sessions = sh.list_sessions()
        assert len(sessions) == 2

    def test_load_session(self, tmp_path):
        data = json.dumps({
            "timestamp": "2026-04-01T00:00:00",
            "session_id": "test1",
            "tenant_id": "tony",
            "user_input": "hello",
            "response": "hi there",
            "tier": 1,
            "complexity_score": 0.2,
            "message_count": 2,
        })
        (tmp_path / "test1.jsonl").write_text(data + "\n")
        sh = SessionHistory(tmp_path)
        entries = sh.load_session("test1")
        assert len(entries) == 1
        assert entries[0].user_input == "hello"
        assert entries[0].response == "hi there"

    def test_rebuild_messages(self, tmp_path):
        data = json.dumps({
            "timestamp": "2026-04-01T00:00:00",
            "session_id": "test1",
            "tenant_id": "tony",
            "user_input": "hello",
            "response": "hi there",
            "tier": 1,
            "complexity_score": 0.2,
            "message_count": 2,
        })
        (tmp_path / "test1.jsonl").write_text(data + "\n")
        sh = SessionHistory(tmp_path)
        msgs = sh.rebuild_messages("test1")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"

    def test_session_exists(self, tmp_path):
        (tmp_path / "abc.jsonl").write_text("")
        sh = SessionHistory(tmp_path)
        assert sh.session_exists("abc") is True
        assert sh.session_exists("nope") is False

    def test_load_missing_session(self, tmp_path):
        sh = SessionHistory(tmp_path)
        entries = sh.load_session("nonexistent")
        assert entries == []


# ── Repo trust scanning ──────────────────────────────────────────


class TestRepoTrustScan:
    def test_detects_claude_md(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("# instructions")
        repl = _make_repl(tmp_path, work_dir=tmp_path)
        repl._renderer = MagicMock()
        repl._trust_gate = True  # truthy so the warning triggers

        repl._scan_repo_trust()
        repl._renderer.print_warning.assert_called_once()
        call_text = repl._renderer.print_warning.call_args[0][0]
        assert "CLAUDE.md" in call_text

    def test_clean_repo_no_warning(self, tmp_path):
        repl = _make_repl(tmp_path, work_dir=tmp_path)
        repl._renderer = MagicMock()
        repl._scan_repo_trust()
        repl._renderer.print_warning.assert_not_called()


# ── Consecutive failure cap ──────────────────────────────────────


class TestFailureCap:
    @pytest.mark.asyncio
    async def test_failure_cap_reached(self, tmp_path):
        repl = _make_repl(tmp_path, max_tool_failures=2)

        # Create a mock tool that always fails
        mock_tool = MagicMock()
        mock_tool.name = "bad_tool"
        mock_tool.is_destructive = False
        mock_tool.validate_input = MagicMock(return_value=None)
        mock_tool.execute = AsyncMock(side_effect=RuntimeError("boom"))
        repl._tools = [mock_tool]

        # Fake tool call object
        tc = MagicMock()
        tc.name = "bad_tool"
        tc.arguments = {}

        # First failure — under cap
        result1 = await repl._execute_tool(tc)
        assert "Tool error" in result1
        assert repl._consecutive_failures == 1

        # Second failure — hits cap
        result2 = await repl._execute_tool(tc)
        assert "cap" in result2.lower()
        assert repl._consecutive_failures == 2


# ── Tier selection ────────────────────────────────────────────────


class TestTierSelection:
    def test_auto_low_score(self, tmp_path):
        repl = _make_repl(tmp_path)
        assert repl._select_tier(0.2) == 1

    def test_auto_mid_score(self, tmp_path):
        repl = _make_repl(tmp_path)
        assert repl._select_tier(0.5) == 2

    def test_auto_high_score(self, tmp_path):
        repl = _make_repl(tmp_path)
        assert repl._select_tier(0.8) == 4

    def test_forced_tier(self, tmp_path):
        repl = _make_repl(tmp_path, model_tier=5)
        assert repl._select_tier(0.1) == 5

    def test_offline_overrides(self, tmp_path):
        repl = _make_repl(tmp_path, offline=True)
        assert repl._select_tier(0.1) == 5


# ── CLIRenderer ──────────────────────────────────────────────────


class TestCLIRenderer:
    def test_init_without_rich(self):
        renderer = CLIRenderer()
        # Should not crash regardless of rich availability
        assert renderer is not None

    def test_print_response_empty(self, capsys):
        renderer = CLIRenderer()
        renderer.print_response("")
        # Empty string -> no output
        captured = capsys.readouterr()
        assert captured.out == ""


# ── One-shot mode (import check) ─────────────────────────────────


class TestOneShot:
    def test_import_repl(self):
        """Basic smoke test: module imports cleanly."""
        from atlas.cli.repl import ATLASRepl

        repl = ATLASRepl()
        assert repl.session_id
        assert len(repl.messages) == 0

    def test_import_main(self):
        from atlas.cli.__main__ import main

        assert callable(main)
