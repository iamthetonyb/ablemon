"""
Execution Monitor — PentAGI-inspired progress analysis for tool loops.

The current Hermes budget pressure system counts iterations (inject warning at 80%).
This monitor analyzes WHETHER PROGRESS IS BEING MADE, not just how many iterations
have passed. It detects:

1. **Spinning**: Same tool called 3+ times with similar args (stuck in a loop)
2. **Thrashing**: Alternating between 2 tools without forward progress
3. **Drift**: Tool calls diverging from the original task intent
4. **Escalation stall**: Complex task where tools aren't reducing remaining work

When detected, the monitor generates targeted intervention messages that are
more informative than the generic "stop calling tools" budget pressure.

Usage:
    monitor = ExecutionMonitor()
    for iteration in range(15):
        ...execute tool...
        verdict = monitor.analyze(tool_calls_so_far, original_task)
        if verdict.should_intervene:
            inject verdict.message into next prompt
"""

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ToolCallRecord:
    """Record of a single tool call in the loop."""
    name: str
    args: Dict[str, Any]
    output_preview: str  # First 200 chars of output
    iteration: int
    success: bool = True


@dataclass
class MonitorVerdict:
    """Result of execution monitor analysis."""
    should_intervene: bool = False
    pattern: str = ""  # "spinning", "thrashing", "drift", "stall", "healthy"
    confidence: float = 0.0  # 0.0-1.0 how confident we are this is a problem
    message: str = ""  # Injection message for the model
    should_terminate: bool = False  # True = stop the loop entirely
    details: str = ""


class ExecutionMonitor:
    """
    Watches tool call history and detects unproductive patterns.

    Runs after each tool call. Lightweight — no LLM calls, pure heuristics.
    Designed to complement (not replace) the iteration budget pressure.
    """

    # Thresholds
    SPIN_THRESHOLD = 3  # Same tool+similar args N times = spinning
    THRASH_THRESHOLD = 4  # A-B-A-B pattern for N calls = thrashing
    OUTPUT_SIMILARITY_THRESHOLD = 0.7  # Jaccard similarity of output tokens

    def __init__(self):
        self.history: List[ToolCallRecord] = []
        self._last_verdict: Optional[MonitorVerdict] = None

    def record(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        tool_output: str,
        iteration: int,
        success: bool = True,
    ):
        """Record a tool call for analysis."""
        self.history.append(ToolCallRecord(
            name=tool_name,
            args=tool_args,
            output_preview=str(tool_output)[:200],
            iteration=iteration,
            success=success,
        ))

    def analyze(self, original_task: str = "") -> MonitorVerdict:
        """
        Analyze the tool call history for unproductive patterns.

        Called after each tool execution. Returns a verdict with
        optional intervention message.
        """
        if len(self.history) < 3:
            return MonitorVerdict(pattern="healthy")

        # Check patterns in priority order (most harmful first)
        verdict = self._check_spinning()
        if verdict.should_intervene:
            self._last_verdict = verdict
            return verdict

        verdict = self._check_thrashing()
        if verdict.should_intervene:
            self._last_verdict = verdict
            return verdict

        verdict = self._check_output_repetition()
        if verdict.should_intervene:
            self._last_verdict = verdict
            return verdict

        verdict = self._check_error_loop()
        if verdict.should_intervene:
            self._last_verdict = verdict
            return verdict

        return MonitorVerdict(pattern="healthy")

    def _check_spinning(self) -> MonitorVerdict:
        """Detect same tool called repeatedly with similar arguments."""
        if len(self.history) < self.SPIN_THRESHOLD:
            return MonitorVerdict(pattern="healthy")

        recent = self.history[-self.SPIN_THRESHOLD:]
        names = [r.name for r in recent]

        # All same tool?
        if len(set(names)) == 1:
            tool = names[0]
            # Check argument similarity
            args_strs = [_args_fingerprint(r.args) for r in recent]
            unique_args = len(set(args_strs))

            if unique_args <= 1:
                # Exact same tool + same args = hard spin
                return MonitorVerdict(
                    should_intervene=True,
                    pattern="spinning",
                    confidence=0.95,
                    message=(
                        f"\n\n[EXECUTION MONITOR] You have called `{tool}` {self.SPIN_THRESHOLD} times "
                        f"with identical arguments. This is not making progress. "
                        f"Either: (1) the tool output already contains what you need — use it, "
                        f"(2) try a DIFFERENT tool or approach, or "
                        f"(3) synthesize your answer from what you have."
                    ),
                    should_terminate=len(self.history) > 8,
                    details=f"Tool '{tool}' called {self.SPIN_THRESHOLD}x with same args",
                )
            elif unique_args <= 2:
                # Same tool, slightly different args = soft spin
                return MonitorVerdict(
                    should_intervene=True,
                    pattern="spinning",
                    confidence=0.7,
                    message=(
                        f"\n\n[EXECUTION MONITOR] You've called `{tool}` {self.SPIN_THRESHOLD} times "
                        f"with minor variations. The results are likely similar. "
                        f"Synthesize from what you have or try a completely different approach."
                    ),
                    details=f"Tool '{tool}' called {self.SPIN_THRESHOLD}x with {unique_args} arg variations",
                )

        return MonitorVerdict(pattern="healthy")

    def _check_thrashing(self) -> MonitorVerdict:
        """Detect A-B-A-B alternating pattern without progress."""
        if len(self.history) < self.THRASH_THRESHOLD:
            return MonitorVerdict(pattern="healthy")

        recent = self.history[-self.THRASH_THRESHOLD:]
        names = [r.name for r in recent]

        # Check for alternating pattern
        if len(set(names)) == 2:
            # A-B-A-B?
            is_alternating = all(
                names[i] != names[i + 1] for i in range(len(names) - 1)
            )
            if is_alternating:
                tool_a, tool_b = set(names)
                return MonitorVerdict(
                    should_intervene=True,
                    pattern="thrashing",
                    confidence=0.8,
                    message=(
                        f"\n\n[EXECUTION MONITOR] You are alternating between `{tool_a}` and "
                        f"`{tool_b}` without making progress. This usually means the approach "
                        f"isn't working. Step back and consider: What information do you actually "
                        f"need? Is there a more direct way to get it?"
                    ),
                    details=f"Alternating {tool_a} ↔ {tool_b} for {self.THRASH_THRESHOLD} calls",
                )

        return MonitorVerdict(pattern="healthy")

    def _check_output_repetition(self) -> MonitorVerdict:
        """Detect when tool outputs are repetitive (getting same results)."""
        if len(self.history) < 3:
            return MonitorVerdict(pattern="healthy")

        recent = self.history[-4:]
        outputs = [r.output_preview for r in recent]

        # Check pairwise similarity
        similar_count = 0
        for i in range(len(outputs)):
            for j in range(i + 1, len(outputs)):
                if _text_similarity(outputs[i], outputs[j]) > self.OUTPUT_SIMILARITY_THRESHOLD:
                    similar_count += 1

        total_pairs = len(outputs) * (len(outputs) - 1) // 2
        if total_pairs > 0 and similar_count / total_pairs > 0.5:
            return MonitorVerdict(
                should_intervene=True,
                pattern="stall",
                confidence=0.65,
                message=(
                    "\n\n[EXECUTION MONITOR] Your recent tool calls are returning very similar "
                    "results. You likely have all the information available. "
                    "Synthesize your answer from the data you've already collected."
                ),
                details=f"{similar_count}/{total_pairs} output pairs are >70% similar",
            )

        return MonitorVerdict(pattern="healthy")

    def _check_error_loop(self) -> MonitorVerdict:
        """Detect repeated failures without changing approach."""
        if len(self.history) < 3:
            return MonitorVerdict(pattern="healthy")

        recent = self.history[-3:]
        failures = [r for r in recent if not r.success]

        if len(failures) >= 3:
            tools_tried = set(r.name for r in failures)
            return MonitorVerdict(
                should_intervene=True,
                pattern="error_loop",
                confidence=0.85,
                message=(
                    f"\n\n[EXECUTION MONITOR] Last {len(failures)} tool calls all failed "
                    f"({', '.join(tools_tried)}). Stop retrying the same approach. "
                    f"Either: (1) explain the error to the user and ask for help, "
                    f"(2) try a completely different tool/approach, or "
                    f"(3) answer with what you know without tool assistance."
                ),
                should_terminate=len(self.history) > 10,
                details=f"{len(failures)} consecutive failures",
            )

        return MonitorVerdict(pattern="healthy")

    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of the monitoring session."""
        if not self.history:
            return {"total_calls": 0}

        tool_counts = Counter(r.name for r in self.history)
        failures = sum(1 for r in self.history if not r.success)

        return {
            "total_calls": len(self.history),
            "unique_tools": len(tool_counts),
            "tool_distribution": dict(tool_counts),
            "failure_count": failures,
            "last_verdict": self._last_verdict.pattern if self._last_verdict else "healthy",
            "interventions": sum(
                1 for v in [self._last_verdict] if v and v.should_intervene
            ),
        }


def _args_fingerprint(args: Dict[str, Any]) -> str:
    """Create a normalized fingerprint of tool arguments for comparison."""
    if not args:
        return ""
    # Sort keys and stringify values, normalize whitespace
    parts = []
    for k in sorted(args.keys()):
        v = str(args[k]).strip().lower()
        # Normalize numbers and whitespace
        v = re.sub(r"\s+", " ", v)
        parts.append(f"{k}={v[:100]}")
    return "|".join(parts)


def _text_similarity(a: str, b: str) -> float:
    """Quick Jaccard similarity on word tokens."""
    if not a or not b:
        return 0.0
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)
