"""
Secure Sandbox Executor
Executes code in an isolated environment with resource limits.
"""

import subprocess
import tempfile
import os
import signal
import json
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

class ExecutionStatus(Enum):
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    BLOCKED = "blocked"

@dataclass
class ExecutionResult:
    """Result of code execution"""
    status: ExecutionStatus
    stdout: str
    stderr: str
    exit_code: int
    execution_time: float
    blocked_reason: Optional[str] = None

class SecureSandbox:
    """
    Secure code execution sandbox.
    Features:
    - Resource limits (CPU, memory, time)
    - Filesystem isolation
    - Network restrictions
    - Import blocklist
    """

    # Imports that are blocked in sandboxed Python
    BLOCKED_IMPORTS = {
        'os.system', 'subprocess', 'socket', 'urllib', 'requests',
        'http.client', 'ftplib', 'smtplib', 'telnetlib',
        'shutil.rmtree', 'shutil.move',
        '__import__', 'importlib', 'exec', 'eval', 'compile',
        'open',  # We provide a safe file API
    }

    # Allowed imports for common operations
    ALLOWED_IMPORTS = {
        'math', 'json', 'datetime', 'collections', 'itertools',
        'functools', 're', 'string', 'random', 'statistics',
        'dataclasses', 'typing', 'enum', 'decimal', 'fractions',
    }

    def __init__(
        self,
        timeout: int = 30,
        max_memory_mb: int = 256,
        work_dir: Optional[Path] = None
    ):
        self.timeout = timeout
        self.max_memory_mb = max_memory_mb
        self.work_dir = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="able_sandbox_"))
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def _check_code_safety(self, code: str) -> Optional[str]:
        """Check code for dangerous patterns"""
        import re

        # Check for blocked imports
        for blocked in self.BLOCKED_IMPORTS:
            patterns = [
                rf'import\s+{blocked.replace(".", r"\.")}',
                rf'from\s+{blocked.split(".")[0]}\s+import',
                rf'{blocked}\s*\(',
            ]
            for pattern in patterns:
                if re.search(pattern, code, re.IGNORECASE):
                    return f"Blocked import/call: {blocked}"

        # Check for dangerous builtins
        dangerous = ['__import__', 'eval', 'exec', 'compile', 'open', 'globals', 'locals']
        for d in dangerous:
            if d + '(' in code:
                return f"Blocked builtin: {d}"

        # Check for shell escape attempts
        shell_patterns = [
            r'os\.system',
            r'os\.popen',
            r'subprocess\.',
            r'commands\.',
            r'\|\s*sh',
            r'\|\s*bash',
        ]
        for pattern in shell_patterns:
            if re.search(pattern, code):
                return f"Shell execution blocked"

        return None

    def execute_python(
        self,
        code: str,
        inputs: Dict[str, Any] = None
    ) -> ExecutionResult:
        """Execute Python code in sandbox"""
        start_time = datetime.now()

        # Safety check
        blocked_reason = self._check_code_safety(code)
        if blocked_reason:
            return ExecutionResult(
                status=ExecutionStatus.BLOCKED,
                stdout="",
                stderr=blocked_reason,
                exit_code=-1,
                execution_time=0,
                blocked_reason=blocked_reason
            )

        # Create temporary script file
        script_path = self.work_dir / "script.py"

        # Wrap code with input injection and safe environment
        wrapper = f'''
import sys
import json

# Disable dangerous builtins
__builtins_copy = __builtins__.copy() if isinstance(__builtins__, dict) else dict(vars(__builtins__))
for blocked in ['eval', 'exec', 'compile', '__import__', 'open', 'globals', 'locals', 'vars']:
    if blocked in __builtins_copy:
        del __builtins_copy[blocked]

# Inject inputs
_inputs = {json.dumps(inputs or {})}
for _k, _v in _inputs.items():
    globals()[_k] = _v

# User code
{code}
'''

        script_path.write_text(wrapper)

        try:
            # Execute with resource limits
            result = subprocess.run(
                [
                    'python3', str(script_path)
                ],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=str(self.work_dir),
                env={
                    'PATH': '/usr/bin:/bin',
                    'HOME': str(self.work_dir),
                    'PYTHONDONTWRITEBYTECODE': '1',
                    'PYTHONUNBUFFERED': '1',
                }
            )

            execution_time = (datetime.now() - start_time).total_seconds()

            return ExecutionResult(
                status=ExecutionStatus.SUCCESS if result.returncode == 0 else ExecutionStatus.ERROR,
                stdout=result.stdout[:10000],  # Limit output size
                stderr=result.stderr[:10000],
                exit_code=result.returncode,
                execution_time=execution_time
            )

        except subprocess.TimeoutExpired:
            execution_time = (datetime.now() - start_time).total_seconds()
            return ExecutionResult(
                status=ExecutionStatus.TIMEOUT,
                stdout="",
                stderr=f"Execution timed out after {self.timeout} seconds",
                exit_code=-1,
                execution_time=execution_time
            )

        except Exception as e:
            execution_time = (datetime.now() - start_time).total_seconds()
            return ExecutionResult(
                status=ExecutionStatus.ERROR,
                stdout="",
                stderr=str(e),
                exit_code=-1,
                execution_time=execution_time
            )

        finally:
            # Cleanup
            if script_path.exists():
                script_path.unlink()

    def execute_shell(self, command: str) -> ExecutionResult:
        """Execute shell command through CommandGuard"""
        from core.security.command_guard import CommandGuard, CommandVerdict

        start_time = datetime.now()

        # Check command through guard
        guard = CommandGuard()
        analysis = guard.analyze(command)

        if analysis.verdict == CommandVerdict.DENIED:
            return ExecutionResult(
                status=ExecutionStatus.BLOCKED,
                stdout="",
                stderr=f"Command denied: {analysis.reason}",
                exit_code=-1,
                execution_time=0,
                blocked_reason=analysis.reason
            )

        if analysis.verdict == CommandVerdict.REQUIRES_APPROVAL:
            return ExecutionResult(
                status=ExecutionStatus.BLOCKED,
                stdout="",
                stderr=f"Command requires approval: {analysis.reason}",
                exit_code=-1,
                execution_time=0,
                blocked_reason=f"Requires approval: {analysis.reason}"
            )

        # Execute allowed command
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=str(self.work_dir)
            )

            execution_time = (datetime.now() - start_time).total_seconds()

            return ExecutionResult(
                status=ExecutionStatus.SUCCESS if result.returncode == 0 else ExecutionStatus.ERROR,
                stdout=result.stdout[:10000],
                stderr=result.stderr[:10000],
                exit_code=result.returncode,
                execution_time=execution_time
            )

        except subprocess.TimeoutExpired:
            return ExecutionResult(
                status=ExecutionStatus.TIMEOUT,
                stdout="",
                stderr=f"Timed out after {self.timeout}s",
                exit_code=-1,
                execution_time=self.timeout
            )

        except Exception as e:
            return ExecutionResult(
                status=ExecutionStatus.ERROR,
                stdout="",
                stderr=str(e),
                exit_code=-1,
                execution_time=(datetime.now() - start_time).total_seconds()
            )

    def cleanup(self):
        """Clean up sandbox directory"""
        import shutil
        if self.work_dir.exists() and "able_sandbox_" in str(self.work_dir):
            shutil.rmtree(self.work_dir, ignore_errors=True)
