"""
Tests for conversation harvesters and the training formatter.
"""

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from able.core.distillation.harvesters.base import (
    BaseHarvester,
    HarvestedConversation,
)
from able.core.distillation.harvesters.claude_code_harvester import (
    ClaudeCodeHarvester,
)
from able.core.distillation.harvesters.able_interaction_harvester import (
    ABLEInteractionHarvester,
)
from able.core.distillation.harvesters.inbox_harvester import InboxHarvester
from able.core.distillation.harvesters.opencli_harvester import OpenCLIHarvester
from able.core.distillation.formatter import TrainingFormatter
from able.core.distillation.models import TrainingPair


# ── HarvestedConversation ──────────────────────────────────────────


class TestHarvestedConversation:
    def test_content_hash_auto_generated(self):
        c = HarvestedConversation(
            id="1",
            source="test",
            messages=[{"role": "user", "content": "hello"}],
            model="test-model",
            timestamp=datetime.now(),
        )
        assert c.content_hash != ""
        assert len(c.content_hash) == 64  # sha256 hex

    def test_same_messages_same_hash(self):
        msgs = [{"role": "user", "content": "hello"}]
        a = HarvestedConversation(
            id="a", source="t", messages=msgs, model="m", timestamp=datetime.now()
        )
        b = HarvestedConversation(
            id="b", source="t", messages=msgs, model="m", timestamp=datetime.now()
        )
        assert a.content_hash == b.content_hash


# ── BaseHarvester domain detection ─────────────────────────────────


class _StubHarvester(BaseHarvester):
    source_name = "stub"

    def harvest(self, source_path=None, since=None):
        return []


class TestDomainDetection:
    def setup_method(self):
        self.h = _StubHarvester()

    def test_coding_domain(self):
        msgs = [
            {"role": "user", "content": "Can you debug this function and fix the traceback?"},
            {"role": "assistant", "content": "The exception is a TypeError in your async function."},
        ]
        assert self.h._detect_domain(msgs) == "coding"

    def test_security_domain(self):
        msgs = [
            {"role": "user", "content": "Check this code for XSS vulnerability and CSRF exploits"},
        ]
        assert self.h._detect_domain(msgs) == "security"

    def test_empty_messages(self):
        assert self.h._detect_domain([]) == ""

    def test_no_match(self):
        msgs = [{"role": "user", "content": "What is the weather today?"}]
        assert self.h._detect_domain(msgs) == ""


# ── Meta-conversation filtering ────────────────────────────────────


class TestMetaConversationFilter:
    def setup_method(self):
        self.h = _StubHarvester()

    def test_meta_only(self):
        msgs = [
            {"role": "user", "content": "ok"},
            {"role": "assistant", "content": "sure"},
        ]
        assert self.h._is_meta_conversation(msgs) is True

    def test_substantive(self):
        msgs = [
            {"role": "user", "content": "Explain how the Python GIL works and its implications for multithreaded programs."},
            {"role": "assistant", "content": "The GIL is a mutex that protects access to Python objects, preventing multiple threads from executing Python bytecode simultaneously."},
        ]
        assert self.h._is_meta_conversation(msgs) is False

    def test_single_substantive_still_meta(self):
        # Need at least 2 substantive messages
        msgs = [
            {"role": "user", "content": "Explain how the Python GIL works and its implications for multithreaded programs."},
            {"role": "assistant", "content": "ok"},
        ]
        assert self.h._is_meta_conversation(msgs) is True


# ── ClaudeCodeHarvester ────────────────────────────────────────────


class TestClaudeCodeHarvester:
    def test_parse_basic_jsonl(self, tmp_path):
        session_file = tmp_path / "session.jsonl"
        records = [
            {"type": "user", "uuid": "u1", "timestamp": "2026-03-22T00:00:00Z",
             "message": {"content": "Explain how Python decorators work with practical examples."}},
            {"type": "assistant", "uuid": "a1", "timestamp": "2026-03-22T00:00:01Z",
             "message": {"model": "claude-opus-4-6", "role": "assistant",
                         "content": [{"type": "text", "text": "Decorators are functions that modify other functions. Here is a practical example using functools.wraps."}]}},
        ]
        session_file.write_text(
            "\n".join(json.dumps(r) for r in records), encoding="utf-8"
        )

        harvester = ClaudeCodeHarvester()
        results = harvester.harvest(source_path=str(tmp_path))
        assert len(results) == 1
        assert results[0].source == "claude_code"
        assert len(results[0].messages) == 2
        assert results[0].messages[0]["role"] == "user"

    def test_extracts_thinking_blocks(self, tmp_path):
        session_file = tmp_path / "session.jsonl"
        records = [
            {"type": "user", "uuid": "u1", "timestamp": "2026-03-22T00:00:00Z",
             "message": {"content": "Explain quantum computing and its real-world applications in cryptography."}},
            {"type": "assistant", "uuid": "a1", "timestamp": "2026-03-22T00:00:01Z",
             "message": {"model": "claude-opus-4-6", "role": "assistant",
                         "content": [
                             {"type": "thinking", "thinking": "User wants quantum computing explained"},
                             {"type": "text", "text": "Quantum computing uses qubits instead of classical bits for computation."},
                         ]}},
        ]
        session_file.write_text(
            "\n".join(json.dumps(r) for r in records), encoding="utf-8"
        )

        harvester = ClaudeCodeHarvester()
        results = harvester.harvest(source_path=str(tmp_path))
        assert len(results) == 1
        assert len(results[0].thinking_blocks) == 1
        assert "quantum" in results[0].thinking_blocks[0].lower()

    def test_content_block_format(self, tmp_path):
        """Anthropic-style content blocks with type: text / thinking."""
        session_file = tmp_path / "session.jsonl"
        records = [
            {"type": "user", "uuid": "u1", "timestamp": "2026-03-22T00:00:00Z",
             "message": {"content": "Explain the difference between TCP and UDP protocols in networking."}},
            {"type": "assistant", "uuid": "a1", "timestamp": "2026-03-22T00:00:01Z",
             "message": {"model": "claude-opus-4-6", "role": "assistant",
                         "content": [
                             {"type": "thinking", "thinking": "Compare TCP and UDP"},
                             {"type": "text", "text": "TCP provides reliable ordered delivery, while UDP is connectionless and faster."},
                         ]}},
        ]
        session_file.write_text(
            "\n".join(json.dumps(r) for r in records), encoding="utf-8"
        )

        harvester = ClaudeCodeHarvester()
        results = harvester.harvest(source_path=str(tmp_path))
        assert len(results) == 1
        assert len(results[0].thinking_blocks) == 1
        assert "TCP" in results[0].messages[1]["content"]

    def test_skips_meta_conversation(self, tmp_path):
        session_file = tmp_path / "session.jsonl"
        records = [
            {"type": "user", "uuid": "u1", "isMeta": True, "timestamp": "2026-03-22T00:00:00Z",
             "message": {"content": "ok"}},
            {"type": "assistant", "uuid": "a1", "timestamp": "2026-03-22T00:00:01Z",
             "message": {"model": "claude-opus-4-6", "role": "assistant",
                         "content": [{"type": "text", "text": "sure"}]}},
        ]
        session_file.write_text(
            "\n".join(json.dumps(r) for r in records), encoding="utf-8"
        )

        harvester = ClaudeCodeHarvester()
        results = harvester.harvest(source_path=str(tmp_path))
        # Meta messages filtered + remaining too short
        assert len(results) == 0

    def test_handles_missing_dir(self):
        harvester = ClaudeCodeHarvester()
        results = harvester.harvest(source_path="/nonexistent/path")
        assert results == []

    def test_tool_use_blocks(self, tmp_path):
        session_file = tmp_path / "session.jsonl"
        records = [
            {"type": "user", "uuid": "u1", "timestamp": "2026-03-22T00:00:00Z",
             "message": {"content": "Read the configuration file and explain what each section does in detail."}},
            {"type": "assistant", "uuid": "a1", "timestamp": "2026-03-22T00:00:01Z",
             "message": {"model": "claude-opus-4-6", "role": "assistant",
                         "content": [
                             {"type": "tool_use", "name": "read_file", "input": {"path": "config.yaml"}},
                             {"type": "text", "text": "The configuration file has three sections: database, cache, and logging."},
                         ]}},
        ]
        session_file.write_text(
            "\n".join(json.dumps(r) for r in records), encoding="utf-8"
        )

        harvester = ClaudeCodeHarvester()
        results = harvester.harvest(source_path=str(tmp_path))
        assert len(results) == 1
        assert len(results[0].tool_uses) == 1


# ── ABLEInteractionHarvester ───────────────────────────────────────


class TestABLEInteractionHarvester:
    def _create_test_db(self, db_path: str):
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS interaction_log (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                message_preview TEXT,
                complexity_score REAL,
                selected_tier INTEGER,
                selected_provider TEXT,
                domain TEXT,
                features TEXT,
                scorer_version INTEGER,
                budget_gated INTEGER DEFAULT 0,
                actual_provider TEXT,
                fallback_used INTEGER DEFAULT 0,
                fallback_chain TEXT,
                latency_ms REAL,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cost_usd REAL DEFAULT 0.0,
                success INTEGER DEFAULT 1,
                error_type TEXT,
                user_correction INTEGER DEFAULT 0,
                user_satisfaction INTEGER,
                escalated INTEGER DEFAULT 0,
                channel TEXT,
                session_id TEXT,
                conversation_turn INTEGER DEFAULT 0
            );
            """
        )
        # Insert a good record
        conn.execute(
            """INSERT INTO interaction_log
               (id, timestamp, message_preview, complexity_score,
                selected_tier, selected_provider, domain, success, actual_provider)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                datetime.now().isoformat(),
                "Explain the difference between process and thread in operating systems.",
                0.5,
                2,
                "openai",
                "coding",
                1,
                "openai",
            ),
        )
        # Insert a failed record (should be filtered)
        conn.execute(
            """INSERT INTO interaction_log
               (id, timestamp, message_preview, complexity_score,
                selected_tier, selected_provider, domain, success)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                datetime.now().isoformat(),
                "This one failed with an error",
                0.3,
                1,
                "openai",
                "coding",
                0,
            ),
        )
        conn.commit()
        conn.close()

    def test_harvest_from_db(self, tmp_path):
        db_path = str(tmp_path / "test_log.db")
        self._create_test_db(db_path)

        harvester = ABLEInteractionHarvester(db_path=db_path)
        results = harvester.harvest()
        assert len(results) == 1
        assert results[0].source == "able"
        assert results[0].domain == "coding"

    def test_handles_missing_db(self):
        harvester = ABLEInteractionHarvester(db_path="/nonexistent/test.db")
        results = harvester.harvest()
        assert results == []

    def test_prefers_corpus_eligible_rows_when_available(self, tmp_path):
        db_path = str(tmp_path / "eligible_log.db")
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE interaction_log (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                message_preview TEXT,
                complexity_score REAL,
                selected_tier INTEGER,
                selected_provider TEXT,
                domain TEXT,
                features TEXT,
                scorer_version INTEGER,
                budget_gated INTEGER DEFAULT 0,
                actual_provider TEXT,
                fallback_used INTEGER DEFAULT 0,
                fallback_chain TEXT,
                latency_ms REAL,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cost_usd REAL DEFAULT 0.0,
                success INTEGER DEFAULT 1,
                error_type TEXT,
                user_correction INTEGER DEFAULT 0,
                user_satisfaction INTEGER,
                escalated INTEGER DEFAULT 0,
                channel TEXT,
                session_id TEXT,
                conversation_turn INTEGER DEFAULT 0,
                corpus_eligible INTEGER DEFAULT 0,
                raw_input TEXT,
                raw_output TEXT
            );
            """
        )
        now = datetime.now().isoformat()
        rows = [
            (
                str(uuid.uuid4()),
                now,
                "Preview only user prompt",
                0.5,
                2,
                "openai",
                "coding",
                1,
                "openai",
                0,
                "Full raw input question about concurrency",
                "Full raw output answer about concurrency",
            ),
            (
                str(uuid.uuid4()),
                now,
                "Eligible row preview",
                0.5,
                2,
                "openai",
                "coding",
                1,
                "openai",
                1,
                "Write a Python function that parses CSV files and returns structured data",
                "Here is a Python function that reads a CSV file and returns a list of dictionaries",
            ),
        ]
        conn.executemany(
            """INSERT INTO interaction_log (
                id, timestamp, message_preview, complexity_score,
                selected_tier, selected_provider, domain, success, actual_provider,
                corpus_eligible, raw_input, raw_output
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()
        conn.close()

        harvester = ABLEInteractionHarvester(db_path=db_path)
        results = harvester.harvest()

        assert len(results) == 1
        assert results[0].messages[0]["content"] == "Write a Python function that parses CSV files and returns structured data"
        assert results[0].messages[1]["content"] == "Here is a Python function that reads a CSV file and returns a list of dictionaries"

    def test_falls_back_when_corpus_columns_missing(self, tmp_path):
        db_path = str(tmp_path / "legacy_log.db")
        self._create_test_db(db_path)

        harvester = ABLEInteractionHarvester(db_path=db_path)
        results = harvester.harvest()

        assert len(results) == 1
        assert results[0].messages[0]["content"].startswith("Explain the difference")


# ── InboxHarvester ─────────────────────────────────────────────────


class TestInboxHarvester:
    def test_json_file(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        data = {
            "messages": [
                {"role": "user", "content": "How do I implement a binary search tree in Python with insertion and deletion?"},
                {"role": "assistant", "content": "Here is a complete BST implementation with insert, delete, and search methods."},
            ],
            "model": "test-model",
        }
        (inbox / "convo.json").write_text(json.dumps(data), encoding="utf-8")

        harvester = InboxHarvester(inbox_dir=inbox)
        results = harvester.harvest()
        assert len(results) == 1
        assert results[0].model == "test-model"
        # File should be moved to processed
        assert not (inbox / "convo.json").exists()
        assert (inbox / "processed" / "convo.json").exists()

    def test_jsonl_file(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        records = [
            {
                "messages": [
                    {"role": "user", "content": "What is the time complexity of quicksort in best, average, and worst cases?"},
                    {"role": "assistant", "content": "Best and average case is O(n log n), worst case is O(n^2) when the pivot is always the smallest or largest element."},
                ],
            },
        ]
        (inbox / "batch.jsonl").write_text(
            "\n".join(json.dumps(r) for r in records), encoding="utf-8"
        )

        harvester = InboxHarvester(inbox_dir=inbox)
        results = harvester.harvest()
        assert len(results) == 1

    def test_txt_file(self, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        text = (
            "What is the difference between a stack and a queue data structure?\n\n"
            "A stack is LIFO (last in, first out) while a queue is FIFO (first in, first out). "
            "Stacks support push and pop operations, queues support enqueue and dequeue."
        )
        (inbox / "convo.txt").write_text(text, encoding="utf-8")

        harvester = InboxHarvester(inbox_dir=inbox)
        results = harvester.harvest()
        assert len(results) == 1
        assert results[0].messages[0]["role"] == "user"
        assert results[0].messages[1]["role"] == "assistant"

    def test_creates_inbox_dir(self, tmp_path):
        inbox = tmp_path / "nonexistent_inbox"
        harvester = InboxHarvester(inbox_dir=inbox)
        harvester.harvest()
        assert inbox.exists()
        assert (inbox / "processed").exists()


# ── OpenCLIHarvester ───────────────────────────────────────────────


class TestOpenCLIHarvester:
    def test_adapter_discovery(self):
        """Adapters in the bundled opencli_adapters/ dir are found."""
        harvester = OpenCLIHarvester()
        assert "chatgpt" in harvester.adapters
        assert "codex" in harvester.adapters
        assert "grok" in harvester.adapters
        assert "antigravity" in harvester.adapters
        assert "cowork" in harvester.adapters

    def test_register_custom_adapter(self, tmp_path):
        adapter_yaml = tmp_path / "custom.yaml"
        adapter_yaml.write_text(
            "platform: custom_platform\ndescription: test\nmodel_name: custom\nharvest_method: file\nfile_patterns: []\n",
            encoding="utf-8",
        )

        harvester = OpenCLIHarvester()
        harvester.register_adapter(str(adapter_yaml))
        assert "custom_platform" in harvester.adapters

    def test_harvest_from_files(self, tmp_path):
        """End-to-end: create an adapter + matching export file, harvest."""
        export_dir = tmp_path / "exports"
        export_dir.mkdir()
        export_file = export_dir / "chat.json"
        data = [
            {
                "messages": [
                    {"role": "user", "content": "Explain the CAP theorem and its implications for distributed database design."},
                    {"role": "assistant", "content": "The CAP theorem states that a distributed system cannot simultaneously guarantee consistency, availability, and partition tolerance."},
                ]
            }
        ]
        export_file.write_text(json.dumps(data), encoding="utf-8")

        adapter_yaml = tmp_path / "test_adapter.yaml"
        adapter_yaml.write_text(
            f"platform: test_plat\nmodel_name: test-model\nharvest_method: file\n"
            f"file_patterns:\n  - '{export_dir}/*.json'\n"
            f"message_path: 'messages'\n"
            f"role_mapping:\n  user: user\n  assistant: assistant\n",
            encoding="utf-8",
        )

        harvester = OpenCLIHarvester(adapters_dir=str(tmp_path))
        results = harvester.harvest_platform("test_plat")
        assert len(results) == 1
        assert results[0].source == "opencli:test_plat"

    def test_missing_adapter(self):
        harvester = OpenCLIHarvester()
        results = harvester.harvest_platform("nonexistent")
        assert results == []


# ── TrainingFormatter ──────────────────────────────────────────────


class TestTrainingFormatter:
    def _make_convo(self, **kwargs) -> HarvestedConversation:
        defaults = dict(
            id="test-1",
            source="test",
            messages=[
                {"role": "user", "content": "What is a monad in functional programming?"},
                {"role": "assistant", "content": "A monad is a design pattern that allows structuring programs generically while managing side effects."},
            ],
            model="test-model",
            timestamp=datetime.now(),
            domain="coding",
        )
        defaults.update(kwargs)
        return HarvestedConversation(**defaults)

    def test_format_produces_chatml(self):
        fmt = TrainingFormatter()
        convo = self._make_convo()
        result = fmt.format(convo)

        assert "conversations" in result
        assert "metadata" in result
        # System message is prepended
        assert result["conversations"][0]["role"] == "system"
        assert result["conversations"][1]["role"] == "user"
        assert result["conversations"][2]["role"] == "assistant"

    def test_format_custom_system_prompt(self):
        fmt = TrainingFormatter()
        convo = self._make_convo()
        result = fmt.format(convo, system_prompt="Custom system prompt")
        assert result["conversations"][0]["content"] == "Custom system prompt"

    def test_metadata_fields(self):
        fmt = TrainingFormatter()
        convo = self._make_convo()
        result = fmt.format(convo)
        meta = result["metadata"]
        assert meta["source"] == "test"
        assert meta["teacher_model"] == "test-model"
        assert meta["domain"] == "coding"
        assert meta["tenant_id"] == "default"
        assert "content_hash" in meta

    def test_format_batch(self):
        fmt = TrainingFormatter()
        convos = [self._make_convo(id=f"t-{i}") for i in range(3)]
        results = fmt.format_batch(convos)
        assert len(results) == 3

    def test_normalize_returns_training_pair_with_quality(self):
        fmt = TrainingFormatter()
        convo = self._make_convo(
            thinking_blocks=["The user wants a conceptual explanation with practical framing."],
            tool_uses=[{"name": "read_file", "input": {"path": "notes.md"}}],
        )
        pair = fmt.normalize(convo)
        assert isinstance(pair, TrainingPair)
        assert pair.prompt.startswith("What is a monad")
        assert pair.response.startswith("A monad is a design pattern")
        assert pair.quality_score > 0.0
        assert pair.thinking is not None

    def test_format_carries_scored_metadata(self):
        fmt = TrainingFormatter()
        convo = self._make_convo()
        result = fmt.format(convo)
        assert result["metadata"]["quality_score"] > 0.0

    def test_deduplicate_by_hash(self):
        fmt = TrainingFormatter()
        msgs = [
            {"role": "user", "content": "What is a linked list and how does it differ from an array?"},
            {"role": "assistant", "content": "A linked list is a data structure where elements are stored in nodes that contain pointers to the next node."},
        ]
        c1 = self._make_convo(id="a", source="platform_a", messages=msgs)
        c2 = self._make_convo(id="b", source="platform_b", messages=msgs)
        batch = fmt.format_batch([c1, c2])
        assert len(batch) == 2
        deduped = fmt.deduplicate(batch)
        assert len(deduped) == 1

    def test_deduplicate_keeps_unique(self):
        fmt = TrainingFormatter()
        c1 = self._make_convo(
            id="a",
            messages=[{"role": "user", "content": "Explain recursion in computer science with examples."},
                      {"role": "assistant", "content": "Recursion is when a function calls itself."}],
        )
        c2 = self._make_convo(
            id="b",
            messages=[{"role": "user", "content": "Explain iteration and its advantages over recursion."},
                      {"role": "assistant", "content": "Iteration uses loops instead of recursive calls."}],
        )
        batch = fmt.format_batch([c1, c2])
        deduped = fmt.deduplicate(batch)
        assert len(deduped) == 2

    def test_deduplicate_pairs_uses_content_hash(self):
        fmt = TrainingFormatter()
        pair = fmt.normalize(self._make_convo(id="a"))
        dup = fmt.normalize(self._make_convo(id="b"))
        deduped = fmt.deduplicate_pairs([pair, dup])
        assert len(deduped) == 1
