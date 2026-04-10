"""Tests for able.core.gateway.execution_monitor — PentAGI-inspired progress analysis."""

import pytest

from able.core.gateway.execution_monitor import (
    ExecutionMonitor,
    MonitorVerdict,
    ToolCallRecord,
    _args_fingerprint,
    _text_similarity,
)


@pytest.fixture
def monitor():
    return ExecutionMonitor()


# ── _args_fingerprint ────────────────────────────────────────────

def test_fingerprint_empty():
    assert _args_fingerprint({}) == ""


def test_fingerprint_deterministic():
    args = {"path": "/tmp/file.txt", "mode": "read"}
    assert _args_fingerprint(args) == _args_fingerprint(args)


def test_fingerprint_order_independent():
    a = {"b": "2", "a": "1"}
    b = {"a": "1", "b": "2"}
    assert _args_fingerprint(a) == _args_fingerprint(b)


def test_fingerprint_normalizes_whitespace():
    a = {"query": "hello   world"}
    b = {"query": "hello world"}
    assert _args_fingerprint(a) == _args_fingerprint(b)


def test_fingerprint_different_args():
    a = {"path": "/tmp/a.txt"}
    b = {"path": "/tmp/b.txt"}
    assert _args_fingerprint(a) != _args_fingerprint(b)


# ── _text_similarity ────────────────────────────────────────────

def test_similarity_identical():
    assert _text_similarity("hello world", "hello world") == 1.0


def test_similarity_empty():
    assert _text_similarity("", "hello") == 0.0
    assert _text_similarity("hello", "") == 0.0


def test_similarity_no_overlap():
    assert _text_similarity("cat dog", "fish bird") == 0.0


def test_similarity_partial():
    sim = _text_similarity("the quick brown fox", "the quick red fox")
    assert 0.4 < sim < 0.9  # Partial overlap


# ── analyze() — healthy ─────────────────────────────────────────

def test_healthy_with_few_calls(monitor):
    monitor.record("tool_a", {"x": 1}, "output", 0)
    monitor.record("tool_b", {"y": 2}, "different output", 0)
    verdict = monitor.analyze()
    assert verdict.pattern == "healthy"
    assert not verdict.should_intervene


def test_healthy_diverse_calls(monitor):
    for i in range(5):
        monitor.record(f"tool_{i}", {"arg": i}, f"unique output {i}", i)
    verdict = monitor.analyze()
    assert verdict.pattern == "healthy"


# ── Spinning detection ───────────────────────────────────────────

def test_spinning_same_tool_same_args(monitor):
    """Same tool + identical args 3 times = hard spin."""
    for i in range(3):
        monitor.record("web_search", {"query": "test"}, f"result {i}", i)
    verdict = monitor.analyze()
    assert verdict.should_intervene
    assert verdict.pattern == "spinning"
    assert verdict.confidence >= 0.9


def test_spinning_same_tool_slight_variation(monitor):
    """Same tool + minor arg variations = soft spin."""
    monitor.record("web_search", {"query": "test a"}, "result", 0)
    monitor.record("web_search", {"query": "test b"}, "result", 1)
    monitor.record("web_search", {"query": "test a"}, "result", 2)
    verdict = monitor.analyze()
    assert verdict.should_intervene
    assert verdict.pattern == "spinning"
    assert verdict.confidence >= 0.6


def test_no_spinning_different_tools(monitor):
    """Different tools = not spinning even if args are similar."""
    monitor.record("tool_a", {"x": 1}, "alpha output from first tool", 0)
    monitor.record("tool_b", {"x": 1}, "beta output from second tool", 1)
    monitor.record("tool_c", {"x": 1}, "gamma output from third tool", 2)
    verdict = monitor.analyze()
    assert verdict.pattern == "healthy"


def test_spinning_triggers_terminate_after_many(monitor):
    """After 8+ calls, spinning should recommend termination."""
    for i in range(9):
        monitor.record("stuck_tool", {"q": "same"}, "same output", i)
    verdict = monitor.analyze()
    assert verdict.should_intervene
    assert verdict.should_terminate


# ── Thrashing detection ──────────────────────────────────────────

def test_thrashing_alternating_pattern(monitor):
    """A-B-A-B pattern = thrashing."""
    monitor.record("read_file", {"path": "a.py"}, "content", 0)
    monitor.record("write_file", {"path": "a.py"}, "written", 1)
    monitor.record("read_file", {"path": "a.py"}, "content", 2)
    monitor.record("write_file", {"path": "a.py"}, "written", 3)
    verdict = monitor.analyze()
    assert verdict.should_intervene
    assert verdict.pattern == "thrashing"


def test_no_thrashing_three_tools(monitor):
    """A-B-C-A pattern is NOT thrashing (3 tools, not 2)."""
    monitor.record("tool_a", {}, "out", 0)
    monitor.record("tool_b", {}, "out", 1)
    monitor.record("tool_c", {}, "out", 2)
    monitor.record("tool_a", {}, "out", 3)
    verdict = monitor.analyze()
    assert verdict.pattern != "thrashing"


# ── Output repetition detection ──────────────────────────────────

def test_output_repetition_identical(monitor):
    """Repeated identical outputs = stall."""
    for i in range(4):
        monitor.record(f"tool_{i}", {"i": i}, "The exact same output every time", i)
    verdict = monitor.analyze()
    assert verdict.should_intervene
    assert verdict.pattern == "stall"


def test_no_repetition_diverse_outputs(monitor):
    """Diverse outputs = healthy."""
    monitor.record("tool_a", {}, "cats are great pets for homes", 0)
    monitor.record("tool_b", {}, "python programming language syntax", 1)
    monitor.record("tool_c", {}, "quantum physics wave function collapse", 2)
    monitor.record("tool_d", {}, "stock market trading financial analysis", 3)
    verdict = monitor.analyze()
    # Should not detect stall — outputs are diverse
    assert verdict.pattern != "stall" or not verdict.should_intervene


# ── Error loop detection ─────────────────────────────────────────

def test_error_loop_three_failures(monitor):
    """3 consecutive failures = error loop."""
    monitor.record("bash", {"cmd": "rm -rf /"}, "Permission denied", 0, success=False)
    monitor.record("bash", {"cmd": "sudo rm"}, "Command not found", 1, success=False)
    monitor.record("bash", {"cmd": "del /f"}, "Not recognized", 2, success=False)
    verdict = monitor.analyze()
    assert verdict.should_intervene
    assert verdict.pattern == "error_loop"
    assert verdict.confidence >= 0.8


def test_no_error_loop_with_success(monitor):
    """A success breaks the error loop pattern."""
    monitor.record("bash", {}, "error", 0, success=False)
    monitor.record("bash", {}, "error", 1, success=False)
    monitor.record("bash", {}, "success!", 2, success=True)
    verdict = monitor.analyze()
    assert verdict.pattern != "error_loop"


def test_error_loop_terminate_after_many(monitor):
    """After 10+ calls, error loop should recommend termination."""
    for i in range(11):
        monitor.record("broken_tool", {}, "error", i, success=False)
    verdict = monitor.analyze()
    assert verdict.should_intervene
    assert verdict.should_terminate


# ── get_summary ──────────────────────────────────────────────────

def test_summary_empty(monitor):
    summary = monitor.get_summary()
    assert summary["total_calls"] == 0


def test_summary_populated(monitor):
    monitor.record("tool_a", {}, "out", 0)
    monitor.record("tool_a", {}, "out", 1)
    monitor.record("tool_b", {}, "out", 2, success=False)
    summary = monitor.get_summary()
    assert summary["total_calls"] == 3
    assert summary["unique_tools"] == 2
    assert summary["failure_count"] == 1
    assert summary["tool_distribution"]["tool_a"] == 2
    assert summary["tool_distribution"]["tool_b"] == 1


# ── ToolCallRecord / MonitorVerdict dataclasses ──────────────────

def test_tool_call_record_defaults():
    r = ToolCallRecord(name="test", args={}, output_preview="out", iteration=0)
    assert r.success is True


def test_monitor_verdict_defaults():
    v = MonitorVerdict()
    assert not v.should_intervene
    assert not v.should_terminate
    assert v.pattern == ""
    assert v.confidence == 0.0


# ── Priority ordering ───────────────────────────────────────────

def test_spinning_takes_priority_over_thrashing(monitor):
    """When both spinning and thrashing could apply, spinning wins."""
    # 4 calls: same tool, same args — both spinning (3+) and could look like thrashing
    for i in range(4):
        monitor.record("tool_x", {"a": 1}, "output", i)
    verdict = monitor.analyze()
    assert verdict.pattern == "spinning"  # Spinning checked first


def test_spinning_takes_priority_over_error_loop(monitor):
    """Spinning checked before error loop."""
    for i in range(3):
        monitor.record("tool_x", {"a": 1}, "error output", i, success=False)
    verdict = monitor.analyze()
    # Could be both spinning and error_loop — spinning wins
    assert verdict.pattern == "spinning"
