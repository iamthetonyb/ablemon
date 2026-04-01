"""
Tests for the CLI session harvester and session replay.
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from atlas.core.distillation.harvesters.base import HarvestedConversation
from atlas.core.distillation.harvesters.cli_session_harvester import (
    CLISessionHarvester,
)
from atlas.cli.session_replay import (
    ReplayPair,
    _load_session,
    replay_batch,
    replay_session,
)


# ── Helpers ───────────────────────────────────────────────────────


def _write_session(path: Path, records: list[dict]) -> Path:
    """Write a list of dicts as a JSONL session file."""
    path.write_text(
        "\n".join(json.dumps(r) for r in records),
        encoding="utf-8",
    )
    return path


def _basic_session_records() -> list[dict]:
    """Two-turn session with substantive content."""
    return [
        {
            "role": "user",
            "content": "Explain how Python decorators work with practical examples and edge cases.",
        },
        {
            "role": "assistant",
            "model": "claude-opus-4-6",
            "content": "Decorators are higher-order functions that wrap another function to extend its behavior without modifying its source code.",
        },
    ]


# ── CLISessionHarvester ──────────────────────────────────────────


class TestCLISessionHarvester:
    def test_harvest_basic_session(self, tmp_path):
        _write_session(tmp_path / "sess_001.jsonl", _basic_session_records())

        harvester = CLISessionHarvester(sessions_dir=tmp_path)
        results = harvester.harvest()
        assert len(results) == 1
        assert results[0].source == "atlas_cli"
        assert len(results[0].messages) == 2
        assert results[0].model == "claude-opus-4-6"

    def test_extracts_thinking_blocks(self, tmp_path):
        records = [
            {
                "role": "user",
                "content": "Explain quantum computing and its implications for modern cryptography.",
            },
            {
                "role": "assistant",
                "model": "claude-opus-4-6",
                "content": "<think>User wants quantum + crypto explained</think>Quantum computing uses qubits for computation.",
            },
        ]
        _write_session(tmp_path / "sess_think.jsonl", records)

        harvester = CLISessionHarvester(sessions_dir=tmp_path)
        results = harvester.harvest()
        assert len(results) == 1
        assert len(results[0].thinking_blocks) == 1
        assert "quantum" in results[0].thinking_blocks[0].lower()
        # Thinking should be stripped from message content
        assert "<think>" not in results[0].messages[1]["content"]

    def test_extracts_structured_thinking_blocks(self, tmp_path):
        records = [
            {
                "role": "user",
                "content": "Explain the difference between TCP and UDP protocols for network applications.",
            },
            {
                "role": "assistant",
                "model": "claude-opus-4-6",
                "content": [
                    {"type": "thinking", "thinking": "Compare TCP and UDP protocols"},
                    {"type": "text", "text": "TCP provides reliable ordered delivery while UDP is connectionless."},
                ],
            },
        ]
        _write_session(tmp_path / "sess_structured.jsonl", records)

        harvester = CLISessionHarvester(sessions_dir=tmp_path)
        results = harvester.harvest()
        assert len(results) == 1
        assert len(results[0].thinking_blocks) == 1
        assert "TCP" in results[0].messages[1]["content"]

    def test_extracts_tool_use(self, tmp_path):
        records = [
            {
                "role": "user",
                "content": "Read the configuration file and explain what each section controls.",
            },
            {
                "role": "assistant",
                "model": "claude-opus-4-6",
                "content": [
                    {"type": "tool_use", "name": "read_file", "input": {"path": "config.yaml"}},
                    {"type": "text", "text": "The configuration has three sections: database, cache, and logging settings."},
                ],
            },
        ]
        _write_session(tmp_path / "sess_tools.jsonl", records)

        harvester = CLISessionHarvester(sessions_dir=tmp_path)
        results = harvester.harvest()
        assert len(results) == 1
        assert len(results[0].tool_uses) == 1
        assert results[0].metadata["has_tool_use"] is True

    def test_skips_meta_conversation(self, tmp_path):
        records = [
            {"role": "user", "content": "ok"},
            {"role": "assistant", "content": "sure"},
        ]
        _write_session(tmp_path / "sess_meta.jsonl", records)

        harvester = CLISessionHarvester(sessions_dir=tmp_path)
        results = harvester.harvest()
        assert len(results) == 0

    def test_skips_too_short_session(self, tmp_path):
        records = [
            {"role": "user", "content": "hi"},
        ]
        _write_session(tmp_path / "sess_short.jsonl", records)

        harvester = CLISessionHarvester(sessions_dir=tmp_path)
        results = harvester.harvest()
        assert len(results) == 0

    def test_handles_missing_dir(self):
        harvester = CLISessionHarvester(sessions_dir="/nonexistent/path")
        results = harvester.harvest()
        assert results == []

    def test_source_path_override(self, tmp_path):
        alt_dir = tmp_path / "alt"
        alt_dir.mkdir()
        _write_session(alt_dir / "sess.jsonl", _basic_session_records())

        harvester = CLISessionHarvester(sessions_dir=tmp_path)
        # source_path should override the constructor dir
        results = harvester.harvest(source_path=str(alt_dir))
        assert len(results) == 1

    def test_since_filter(self, tmp_path):
        _write_session(tmp_path / "sess.jsonl", _basic_session_records())

        harvester = CLISessionHarvester(sessions_dir=tmp_path)
        # Use a far-future cutoff
        far_future = datetime(2099, 1, 1, tzinfo=timezone.utc)
        results = harvester.harvest(since=far_future)
        assert len(results) == 0

    def test_domain_detection(self, tmp_path):
        records = [
            {
                "role": "user",
                "content": "Check this code for XSS vulnerability and CSRF exploits in the authentication flow.",
            },
            {
                "role": "assistant",
                "content": "I found a potential XSS vulnerability in the input handler and a missing CSRF token validation.",
            },
        ]
        _write_session(tmp_path / "sess_sec.jsonl", records)

        harvester = CLISessionHarvester(sessions_dir=tmp_path)
        results = harvester.harvest()
        assert len(results) == 1
        assert results[0].domain == "security"

    def test_multiple_sessions(self, tmp_path):
        _write_session(tmp_path / "sess_a.jsonl", _basic_session_records())
        _write_session(
            tmp_path / "sess_b.jsonl",
            [
                {"role": "user", "content": "Explain how the Python GIL works and its implications for concurrency."},
                {"role": "assistant", "content": "The GIL prevents multiple threads from executing Python bytecode simultaneously."},
            ],
        )

        harvester = CLISessionHarvester(sessions_dir=tmp_path)
        results = harvester.harvest()
        assert len(results) == 2

    def test_dedup_against_corpus(self, tmp_path):
        _write_session(tmp_path / "sess_dup.jsonl", _basic_session_records())

        harvester = CLISessionHarvester(sessions_dir=tmp_path)
        results = harvester.harvest()
        assert len(results) == 1

        # Simulate existing corpus with the same instruction hash
        import hashlib
        existing = set()
        first_user_msg = results[0].messages[0]["content"]
        existing.add(hashlib.sha256(first_user_msg.encode()).hexdigest())

        deduped = harvester.dedup_against_corpus(results, existing)
        assert len(deduped) == 0

    def test_ignores_non_jsonl_files(self, tmp_path):
        _write_session(tmp_path / "sess.jsonl", _basic_session_records())
        (tmp_path / "notes.txt").write_text("not a session file")

        harvester = CLISessionHarvester(sessions_dir=tmp_path)
        results = harvester.harvest()
        assert len(results) == 1

    def test_content_hash_generated(self, tmp_path):
        _write_session(tmp_path / "sess.jsonl", _basic_session_records())

        harvester = CLISessionHarvester(sessions_dir=tmp_path)
        results = harvester.harvest()
        assert len(results) == 1
        assert results[0].content_hash != ""
        assert len(results[0].content_hash) == 64  # sha256 hex

    def test_malformed_json_lines_skipped(self, tmp_path):
        content = (
            '{"role": "user", "content": "Explain binary search trees and their time complexity."}\n'
            'this is not json\n'
            '{"role": "assistant", "content": "A BST provides O(log n) average time for search, insert, and delete."}\n'
        )
        (tmp_path / "sess_bad.jsonl").write_text(content, encoding="utf-8")

        harvester = CLISessionHarvester(sessions_dir=tmp_path)
        results = harvester.harvest()
        assert len(results) == 1
        assert len(results[0].messages) == 2


# ── ReplayPair ────────────────────────────────────────────────────


class TestReplayPair:
    def test_content_hash_auto_generated(self):
        pair = ReplayPair(
            id="1",
            session_id="sess_1",
            user_message="explain decorators",
            teacher_response="decorators wrap functions",
            teacher_model="opus",
            student_response="decorators modify functions",
            student_model="qwen",
            target_tier=1,
        )
        assert pair.content_hash != ""
        assert len(pair.content_hash) == 64

    def test_to_chatml(self):
        pair = ReplayPair(
            id="1",
            session_id="sess_1",
            user_message="explain decorators",
            teacher_response="decorators wrap functions",
            teacher_model="opus",
            student_response="decorators modify functions",
            student_model="qwen",
            target_tier=1,
            domain="coding",
        )
        chatml = pair.to_chatml(system_prompt="You are ATLAS.")
        assert chatml["conversations"][0]["role"] == "system"
        assert chatml["conversations"][1]["role"] == "user"
        assert chatml["conversations"][2]["role"] == "assistant"
        assert chatml["metadata"]["source"] == "session_replay"
        assert chatml["metadata"]["teacher_model"] == "opus"

    def test_to_chatml_no_system_prompt(self):
        pair = ReplayPair(
            id="1",
            session_id="sess_1",
            user_message="hello",
            teacher_response="hi",
            teacher_model="opus",
            student_response="hey",
            student_model="qwen",
            target_tier=1,
        )
        chatml = pair.to_chatml()
        assert chatml["conversations"][0]["role"] == "user"


# ── Session loading ───────────────────────────────────────────────


class TestLoadSession:
    def test_load_basic(self, tmp_path):
        _write_session(tmp_path / "sess_abc.jsonl", _basic_session_records())
        messages, model = _load_session("sess_abc", sessions_dir=tmp_path)
        assert len(messages) == 2
        assert model == "claude-opus-4-6"

    def test_partial_id_match(self, tmp_path):
        _write_session(tmp_path / "sess_abc123_full.jsonl", _basic_session_records())
        messages, model = _load_session("abc123", sessions_dir=tmp_path)
        assert len(messages) == 2

    def test_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            _load_session("nonexistent", sessions_dir=tmp_path)

    def test_content_block_flattening(self, tmp_path):
        records = [
            {"role": "user", "content": "Explain something complex about distributed systems and consensus."},
            {
                "role": "assistant",
                "model": "opus",
                "content": [
                    {"type": "thinking", "thinking": "internal reasoning"},
                    {"type": "text", "text": "Distributed consensus requires agreement across nodes."},
                ],
            },
        ]
        _write_session(tmp_path / "sess_blocks.jsonl", records)
        messages, _ = _load_session("sess_blocks", sessions_dir=tmp_path)
        assert len(messages) == 2
        # Thinking blocks are not included as text, only text blocks
        assert "internal reasoning" not in messages[1]["content"]
        assert "consensus" in messages[1]["content"]


# ── Session replay ────────────────────────────────────────────────


class TestReplaySession:
    def test_replay_produces_pairs(self, tmp_path):
        _write_session(tmp_path / "sess_replay.jsonl", _basic_session_records())

        pairs = asyncio.run(
            replay_session("sess_replay", target_tier=5, sessions_dir=tmp_path)
        )
        assert len(pairs) == 1
        assert pairs[0].teacher_model == "claude-opus-4-6"
        assert pairs[0].session_id == "sess_replay"
        assert pairs[0].target_tier == 5

    def test_replay_empty_session(self, tmp_path):
        _write_session(tmp_path / "empty.jsonl", [])
        pairs = asyncio.run(
            replay_session("empty", target_tier=1, sessions_dir=tmp_path)
        )
        assert pairs == []

    def test_replay_multi_turn(self, tmp_path):
        records = [
            {"role": "user", "content": "What is a binary tree data structure?"},
            {"role": "assistant", "model": "opus", "content": "A binary tree is a tree where each node has at most two children."},
            {"role": "user", "content": "How do you traverse one in order?"},
            {"role": "assistant", "model": "opus", "content": "In-order traversal visits left subtree, root, then right subtree."},
        ]
        _write_session(tmp_path / "multi.jsonl", records)

        pairs = asyncio.run(
            replay_session("multi", target_tier=1, sessions_dir=tmp_path)
        )
        assert len(pairs) == 2
        assert "binary tree" in pairs[0].user_message
        assert "traverse" in pairs[1].user_message

    def test_replay_batch(self, tmp_path):
        _write_session(tmp_path / "batch_a.jsonl", _basic_session_records())
        _write_session(
            tmp_path / "batch_b.jsonl",
            [
                {"role": "user", "content": "What is the GIL in Python and how does it affect multithreading?"},
                {"role": "assistant", "model": "opus", "content": "The GIL prevents concurrent Python bytecode execution across threads."},
            ],
        )

        pairs = asyncio.run(
            replay_batch(["batch_a", "batch_b"], target_tier=1, sessions_dir=tmp_path)
        )
        assert len(pairs) == 2

    def test_replay_batch_skips_failures(self, tmp_path):
        _write_session(tmp_path / "good.jsonl", _basic_session_records())
        # "bad" doesn't exist — should be skipped, not crash

        pairs = asyncio.run(
            replay_batch(["good", "bad_nonexistent"], target_tier=1, sessions_dir=tmp_path)
        )
        assert len(pairs) == 1
