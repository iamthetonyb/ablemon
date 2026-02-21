"""
Iterative Problem Solver

Wraps task execution with automatic retry logic using different approaches.
Implements the "NEVER SAY CAN'T" protocol by forcing tool attempts before giving up.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple
import traceback

logger = logging.getLogger(__name__)


class AttemptStrategy(str, Enum):
    """Different strategies for solving a problem"""
    PRIMARY = "primary"           # First, most direct approach
    ALTERNATIVE = "alternative"   # Different tool or method
    CREATIVE = "creative"         # Unconventional approach
    DECOMPOSED = "decomposed"     # Break into sub-tasks
    DELEGATED = "delegated"       # Ask for help / escalate


class AttemptStatus(str, Enum):
    """Status of an attempt"""
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"
    BLOCKED = "blocked"


@dataclass
class ToolAttempt:
    """Record of a single tool attempt"""
    tool_name: str
    strategy: AttemptStrategy
    input_args: Dict[str, Any]
    output: Any = None
    error: Optional[str] = None
    status: AttemptStatus = AttemptStatus.FAILED
    execution_time: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class SolverResult:
    """Result of the iterative solving process"""
    success: bool
    output: Any
    attempts: List[ToolAttempt]
    total_time: float
    final_strategy: Optional[AttemptStrategy] = None
    message: str = ""

    def to_report(self) -> str:
        """Generate human-readable report of attempts"""
        lines = []
        lines.append(f"Result: {'SUCCESS' if self.success else 'FAILED'}")
        lines.append(f"Total attempts: {len(self.attempts)}")
        lines.append(f"Total time: {self.total_time:.2f}s")
        lines.append("")
        lines.append("Attempts:")
        for i, attempt in enumerate(self.attempts, 1):
            lines.append(f"  {i}. [{attempt.strategy.value}] {attempt.tool_name}")
            lines.append(f"     Status: {attempt.status.value}")
            if attempt.error:
                lines.append(f"     Error: {attempt.error[:100]}...")
            lines.append(f"     Time: {attempt.execution_time:.2f}s")
        return "\n".join(lines)


class ToolRegistry:
    """
    Registry of available tools that can be used for solving problems.
    Tools are categorized by their capability type.
    """

    def __init__(self):
        self.tools: Dict[str, Callable] = {}
        self.tool_categories: Dict[str, List[str]] = {
            "web_access": [],
            "file_access": [],
            "code_execution": [],
            "external_api": [],
            "memory": [],
            "communication": [],
        }

    def register(
        self,
        name: str,
        handler: Callable,
        categories: List[str] = None,
    ):
        """Register a tool"""
        self.tools[name] = handler
        for cat in (categories or []):
            if cat in self.tool_categories:
                self.tool_categories[cat].append(name)

    def get_tools_for_task(self, task_type: str) -> List[str]:
        """Get relevant tools for a task type"""
        task_tool_map = {
            "fetch_url": ["web_search", "browser", "fetch_url", "curl"],
            "read_file": ["file_read", "shell"],
            "execute_code": ["sandbox", "shell"],
            "search": ["web_search", "memory_recall", "grep"],
            "api_call": ["mcp", "http_request"],
        }
        return task_tool_map.get(task_type, list(self.tools.keys()))


class IterativeSolver:
    """
    Implements the iterative problem-solving loop.

    Flow:
    1. Analyze task → Identify relevant tools
    2. Attempt 1 (Primary) → Try most direct approach
    3. Attempt 2 (Alternative) → Try different tool/method
    4. Attempt 3 (Creative) → Try unconventional approach
    5. Report → Explain all attempts made
    """

    def __init__(
        self,
        tool_registry: ToolRegistry = None,
        max_attempts: int = 3,
        timeout_per_attempt: float = 30.0,
    ):
        self.registry = tool_registry or ToolRegistry()
        self.max_attempts = max_attempts
        self.timeout_per_attempt = timeout_per_attempt

        # Default tool implementations (can be overridden)
        self._default_tools = {}

    def register_default_tools(self, tools: Dict[str, Callable]):
        """Register default tool implementations"""
        self._default_tools.update(tools)
        for name, handler in tools.items():
            self.registry.register(name, handler)

    async def solve(
        self,
        task: str,
        context: Dict[str, Any] = None,
        required_tools: List[str] = None,
    ) -> SolverResult:
        """
        Attempt to solve a task using iterative approaches.

        Args:
            task: Description of what needs to be done
            context: Additional context (URLs, file paths, etc.)
            required_tools: Specific tools to try

        Returns:
            SolverResult with success status, output, and attempt history
        """
        context = context or {}
        start_time = asyncio.get_event_loop().time()
        attempts = []

        # Analyze task to determine tool order
        tools_to_try = self._analyze_task(task, context, required_tools)

        strategies = [
            AttemptStrategy.PRIMARY,
            AttemptStrategy.ALTERNATIVE,
            AttemptStrategy.CREATIVE,
        ]

        for i, (tool_name, strategy) in enumerate(zip(tools_to_try, strategies)):
            if i >= self.max_attempts:
                break

            attempt = await self._make_attempt(
                tool_name=tool_name,
                strategy=strategy,
                task=task,
                context=context,
            )
            attempts.append(attempt)

            # Success - return immediately
            if attempt.status == AttemptStatus.SUCCESS:
                return SolverResult(
                    success=True,
                    output=attempt.output,
                    attempts=attempts,
                    total_time=asyncio.get_event_loop().time() - start_time,
                    final_strategy=strategy,
                    message=f"Solved using {tool_name} ({strategy.value} approach)",
                )

            # Partial success - might be good enough
            if attempt.status == AttemptStatus.PARTIAL:
                logger.info(f"Partial success with {tool_name}, continuing...")

        # All attempts failed
        return SolverResult(
            success=False,
            output=None,
            attempts=attempts,
            total_time=asyncio.get_event_loop().time() - start_time,
            message=self._generate_failure_report(task, attempts),
        )

    def _analyze_task(
        self,
        task: str,
        context: Dict[str, Any],
        required_tools: List[str] = None,
    ) -> List[str]:
        """Analyze task to determine which tools to try"""
        if required_tools:
            return required_tools

        task_lower = task.lower()
        tools = []

        # URL fetching
        if any(w in task_lower for w in ["url", "http", "website", "page", "read from"]):
            tools.extend(["web_search", "browser", "fetch_url"])

        # File operations
        if any(w in task_lower for w in ["file", "read", "write", "path"]):
            tools.extend(["file_read", "file_write", "shell"])

        # Search/research
        if any(w in task_lower for w in ["search", "find", "look up", "research"]):
            tools.extend(["web_search", "memory_recall", "grep"])

        # Code execution
        if any(w in task_lower for w in ["run", "execute", "code", "script"]):
            tools.extend(["sandbox", "shell"])

        # API calls
        if any(w in task_lower for w in ["api", "call", "request", "endpoint"]):
            tools.extend(["mcp", "http_request"])

        # Default fallback
        if not tools:
            tools = ["web_search", "shell", "mcp"]

        # Deduplicate while preserving order
        seen = set()
        return [t for t in tools if not (t in seen or seen.add(t))]

    async def _make_attempt(
        self,
        tool_name: str,
        strategy: AttemptStrategy,
        task: str,
        context: Dict[str, Any],
    ) -> ToolAttempt:
        """Make a single attempt using a tool"""
        start_time = asyncio.get_event_loop().time()

        attempt = ToolAttempt(
            tool_name=tool_name,
            strategy=strategy,
            input_args={"task": task, "context": context},
        )

        try:
            # Get tool handler
            handler = self.registry.tools.get(tool_name)
            if not handler:
                handler = self._default_tools.get(tool_name)

            if not handler:
                attempt.error = f"Tool '{tool_name}' not available"
                attempt.status = AttemptStatus.BLOCKED
                return attempt

            # Execute with timeout
            try:
                if asyncio.iscoroutinefunction(handler):
                    result = await asyncio.wait_for(
                        handler(task, context),
                        timeout=self.timeout_per_attempt,
                    )
                else:
                    result = handler(task, context)

                attempt.output = result
                attempt.status = AttemptStatus.SUCCESS

            except asyncio.TimeoutError:
                attempt.error = f"Timeout after {self.timeout_per_attempt}s"
                attempt.status = AttemptStatus.FAILED

        except Exception as e:
            attempt.error = str(e)
            attempt.status = AttemptStatus.FAILED
            logger.error(f"Attempt failed: {tool_name} - {e}")
            logger.debug(traceback.format_exc())

        attempt.execution_time = asyncio.get_event_loop().time() - start_time
        return attempt

    def _generate_failure_report(
        self,
        task: str,
        attempts: List[ToolAttempt],
    ) -> str:
        """Generate a report explaining what was tried"""
        lines = [
            "I attempted multiple approaches but couldn't complete this task.",
            "",
            "What I tried:",
        ]

        for i, attempt in enumerate(attempts, 1):
            lines.append(f"  {i}. {attempt.tool_name} ({attempt.strategy.value})")
            if attempt.error:
                lines.append(f"     → Failed: {attempt.error}")
            elif attempt.status == AttemptStatus.BLOCKED:
                lines.append(f"     → Blocked: Tool not available")

        lines.append("")
        lines.append("To complete this task, I would need:")

        # Suggest what's needed
        task_lower = task.lower()
        if "url" in task_lower or "http" in task_lower:
            lines.append("  - Web browsing capability or fetch_url tool")
        if "file" in task_lower:
            lines.append("  - File system access")
        if "api" in task_lower:
            lines.append("  - API credentials or MCP tool connection")

        return "\n".join(lines)


class NeverSaysCantWrapper:
    """
    Wraps any response generator to enforce the NEVER SAY CAN'T protocol.

    If the underlying system tries to say "I can't", this wrapper
    intercepts it and forces tool attempts first.
    """

    CANT_PATTERNS = [
        "i cannot",
        "i can't",
        "i don't have access",
        "i'm not able to",
        "i am not able to",
        "i don't have the ability",
        "i'm unable to",
        "i am unable to",
        "outside my capabilities",
        "beyond my capabilities",
        "i lack the ability",
        "not within my capabilities",
    ]

    def __init__(self, solver: IterativeSolver):
        self.solver = solver

    def would_say_cant(self, response: str) -> bool:
        """Check if response contains a 'can't' pattern"""
        response_lower = response.lower()
        return any(pattern in response_lower for pattern in self.CANT_PATTERNS)

    async def intercept_and_try(
        self,
        original_response: str,
        task: str,
        context: Dict[str, Any] = None,
    ) -> Tuple[bool, str]:
        """
        If response says 'can't', try tools first.

        Returns:
            (intercepted, new_response) - True if we intercepted and tried tools
        """
        if not self.would_say_cant(original_response):
            return False, original_response

        logger.info(f"Intercepted 'can't' response, attempting tools...")

        # Try to solve with tools
        result = await self.solver.solve(task, context)

        if result.success:
            return True, f"Actually, I was able to complete this:\n\n{result.output}"
        else:
            # Still failed, but at least we tried
            return True, f"{result.message}\n\nOriginal response: {original_response}"
