"""Tests for D17 — Semantic Tool Abstraction Layer.

Covers: schema registry, context generation, error matching,
categories, built-in schemas.
"""

import pytest

from able.core.gateway.tool_schemas import (
    ErrorPattern,
    IOExample,
    ToolSchema,
    ToolSchemaRegistry,
)


@pytest.fixture
def registry():
    return ToolSchemaRegistry()


@pytest.fixture
def empty_registry():
    return ToolSchemaRegistry(load_builtins=False)


# ── Schema structure ─────────────────────────────────────────────

class TestSchemaStructure:

    def test_basic_schema(self):
        s = ToolSchema(
            name="test_tool",
            description="A test tool",
            semantic_category="testing",
        )
        assert s.name == "test_tool"
        assert s.side_effects is False
        assert s.examples == []

    def test_schema_with_examples(self):
        s = ToolSchema(
            name="test",
            description="test",
            semantic_category="test",
            examples=[IOExample(input={"a": 1}, output="result")],
        )
        assert len(s.examples) == 1

    def test_error_pattern(self):
        ep = ErrorPattern(
            error_type="not_found",
            pattern="FileNotFoundError",
            description="File missing",
            recovery="Check path",
        )
        assert ep.error_type == "not_found"


# ── Built-in schemas ─────────────────────────────────────────────

class TestBuiltinSchemas:

    def test_builtins_loaded(self, registry):
        assert registry.get("read_file") is not None
        assert registry.get("write_file") is not None
        assert registry.get("shell") is not None

    def test_read_file_has_examples(self, registry):
        s = registry.get("read_file")
        assert len(s.examples) >= 1

    def test_write_file_has_side_effects(self, registry):
        s = registry.get("write_file")
        assert s.side_effects is True
        assert s.requires_approval is True

    def test_shell_has_error_patterns(self, registry):
        s = registry.get("shell")
        assert len(s.error_patterns) >= 1


# ── Registry operations ──────────────────────────────────────────

class TestRegistryOperations:

    def test_register_custom(self, empty_registry):
        schema = ToolSchema(
            name="custom",
            description="Custom tool",
            semantic_category="custom",
        )
        empty_registry.register(schema)
        assert empty_registry.get("custom") is not None

    def test_get_nonexistent(self, registry):
        assert registry.get("nonexistent") is None

    def test_categories(self, registry):
        cats = registry.categories()
        assert "filesystem" in cats
        assert "shell" in cats

    def test_by_category(self, registry):
        fs_tools = registry.by_category("filesystem")
        assert len(fs_tools) >= 2  # read_file, write_file

    def test_stats(self, registry):
        stats = registry.stats()
        assert stats["total_schemas"] >= 5
        assert stats["with_examples"] >= 3
        assert stats["side_effect_tools"] >= 2


# ── Context generation ───────────────────────────────────────────

class TestContextGeneration:

    def test_generate_all(self, registry):
        ctx = registry.generate_tool_context()
        assert "Available tools:" in ctx
        assert "read_file" in ctx

    def test_generate_specific(self, registry):
        ctx = registry.generate_tool_context(["read_file", "shell"])
        assert "read_file" in ctx
        assert "shell" in ctx

    def test_generate_empty(self, empty_registry):
        ctx = empty_registry.generate_tool_context()
        assert ctx == ""

    def test_token_budget_respected(self, registry):
        short = registry.generate_tool_context(max_tokens=50)
        long = registry.generate_tool_context(max_tokens=5000)
        assert len(short) <= len(long)

    def test_contains_examples(self, registry):
        ctx = registry.generate_tool_context(["read_file"])
        assert "Example:" in ctx

    def test_contains_error_recovery(self, registry):
        ctx = registry.generate_tool_context(["read_file"])
        assert "On error:" in ctx


# ── Error matching ───────────────────────────────────────────────

class TestErrorMatching:

    def test_match_known_error(self, registry):
        ep = registry.match_error("read_file", "FileNotFoundError: no such file")
        assert ep is not None
        assert ep.error_type == "not_found"

    def test_match_case_insensitive(self, registry):
        ep = registry.match_error("shell", "TIMEOUTERROR occurred")
        assert ep is not None

    def test_no_match(self, registry):
        ep = registry.match_error("read_file", "some random error")
        assert ep is None

    def test_match_unknown_tool(self, registry):
        ep = registry.match_error("nonexistent_tool", "any error")
        assert ep is None
