"""
Skill Executor - Secure execution of skills.
"""

import asyncio
import logging
import sys
import importlib.util
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List

from .registry import Skill, SkillRegistry

logger = logging.getLogger(__name__)


@dataclass
class SkillResult:
    """Result of skill execution"""
    success: bool
    output: Any = None
    error: Optional[str] = None
    execution_time_ms: float = 0.0
    logs: List[str] = field(default_factory=list)
    artifacts: Dict[str, Path] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "execution_time_ms": self.execution_time_ms,
            "logs": self.logs,
            "artifacts": {k: str(v) for k, v in self.artifacts.items()}
        }


class SkillExecutor:
    """
    Executes skills with security checks and sandboxing.

    Features:
    - Trust level validation
    - Approval workflow integration
    - Sandboxed execution
    - Timeout enforcement
    - Audit logging
    """

    def __init__(
        self,
        registry: SkillRegistry,
        trust_gate=None,
        approval_workflow=None,
        sandbox=None,
        default_timeout: float = 60.0
    ):
        self.registry = registry
        self.trust_gate = trust_gate
        self.approval_workflow = approval_workflow
        self.sandbox = sandbox
        self.default_timeout = default_timeout

    async def execute(
        self,
        skill_name: str,
        args: Dict[str, Any],
        user_trust_tier: str = "L2_SUGGEST",
        timeout: Optional[float] = None,
        require_approval: Optional[bool] = None
    ) -> SkillResult:
        """
        Execute a skill.

        Args:
            skill_name: Name of the skill to execute
            args: Arguments to pass to the skill
            user_trust_tier: Trust level of the requesting user
            timeout: Execution timeout in seconds
            require_approval: Override skill's approval requirement

        Returns:
            SkillResult with output or error
        """
        start_time = datetime.utcnow()

        # Get skill
        skill = self.registry.get(skill_name)
        if not skill:
            return SkillResult(
                success=False,
                error=f"Skill not found: {skill_name}"
            )

        # Check trust level
        trust_levels = ["L1_OBSERVE", "L2_SUGGEST", "L3_BOUNDED", "L4_AUTONOMOUS"]
        user_level = trust_levels.index(user_trust_tier) if user_trust_tier in trust_levels else 0
        required_level = trust_levels.index(skill.metadata.trust_level_required) if skill.metadata.trust_level_required in trust_levels else 1

        if user_level < required_level:
            return SkillResult(
                success=False,
                error=f"Insufficient trust level. Required: {skill.metadata.trust_level_required}, Have: {user_trust_tier}"
            )

        # Security check on arguments
        if self.trust_gate:
            import json
            verdict = self.trust_gate.evaluate(
                json.dumps(args),
                source=f"skill:{skill_name}",
                user_trust_tier=user_trust_tier
            )
            if not verdict.get('allowed', True):
                return SkillResult(
                    success=False,
                    error=f"Security check failed: {verdict.get('reason', 'Unknown')}"
                )

        # Check if approval required
        needs_approval = require_approval if require_approval is not None else skill.metadata.requires_approval

        if needs_approval and self.approval_workflow:
            approval = await self.approval_workflow.request_approval(
                operation=f"skill:{skill_name}",
                details={"args": args},
                risk_level="medium"
            )
            if approval.status.value != "approved":
                return SkillResult(
                    success=False,
                    error=f"Approval denied: {approval.reason or 'No reason given'}"
                )

        # Execute based on implementation type
        try:
            timeout = timeout or self.default_timeout

            if skill.implementation_type == "callable":
                result = await self._execute_callable(skill, args, timeout)
            elif skill.implementation_type == "python":
                result = await self._execute_python(skill, args, timeout)
            elif skill.implementation_type == "bash":
                result = await self._execute_bash(skill, args, timeout)
            elif skill.implementation_type == "bun":
                result = await self._execute_bun(skill, args, timeout)
            else:
                return SkillResult(
                    success=False,
                    error=f"Unknown implementation type: {skill.implementation_type}"
                )

            # Update usage statistics
            self.registry.update_usage(skill_name)

            # Calculate execution time
            result.execution_time_ms = (datetime.utcnow() - start_time).total_seconds() * 1000

            return result

        except asyncio.TimeoutError:
            return SkillResult(
                success=False,
                error=f"Skill execution timed out after {timeout}s"
            )
        except Exception as e:
            logger.exception(f"Skill execution failed: {skill_name}")
            return SkillResult(
                success=False,
                error=str(e)
            )

    async def _execute_callable(
        self,
        skill: Skill,
        args: Dict[str, Any],
        timeout: float
    ) -> SkillResult:
        """Execute a callable skill"""
        if not skill.callable:
            return SkillResult(success=False, error="Callable not set")

        if asyncio.iscoroutinefunction(skill.callable):
            output = await asyncio.wait_for(
                skill.callable(**args),
                timeout=timeout
            )
        else:
            output = await asyncio.wait_for(
                asyncio.to_thread(skill.callable, **args),
                timeout=timeout
            )

        return SkillResult(success=True, output=output)

    async def _execute_python(
        self,
        skill: Skill,
        args: Dict[str, Any],
        timeout: float
    ) -> SkillResult:
        """Execute a Python skill"""
        if self.sandbox:
            # Use sandbox for execution
            code = skill.implementation_path.read_text()
            return await asyncio.wait_for(
                self.sandbox.execute_python(code, args),
                timeout=timeout
            )

        # Direct execution (less safe, for trusted skills)
        spec = importlib.util.spec_from_file_location(
            skill.metadata.name,
            skill.implementation_path
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[skill.metadata.name] = module

        try:
            spec.loader.exec_module(module)

            # Look for main function
            if hasattr(module, 'main'):
                if asyncio.iscoroutinefunction(module.main):
                    output = await asyncio.wait_for(
                        module.main(args),
                        timeout=timeout
                    )
                else:
                    output = await asyncio.wait_for(
                        asyncio.to_thread(module.main, args),
                        timeout=timeout
                    )
                return SkillResult(success=True, output=output)
            else:
                return SkillResult(
                    success=False,
                    error="Skill has no main() function"
                )
        finally:
            del sys.modules[skill.metadata.name]

    async def _execute_bash(
        self,
        skill: Skill,
        args: Dict[str, Any],
        timeout: float
    ) -> SkillResult:
        """Execute a bash skill"""
        import shlex

        # Build command with arguments
        cmd = str(skill.implementation_path)
        for key, value in args.items():
            cmd += f" --{key}={shlex.quote(str(value))}"

        if self.sandbox:
            # Use sandbox's secure shell
            return await asyncio.wait_for(
                self.sandbox.execute_shell(cmd),
                timeout=timeout
            )

        # Direct execution
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout
            )

            if proc.returncode == 0:
                return SkillResult(
                    success=True,
                    output=stdout.decode(),
                    logs=[stderr.decode()] if stderr else []
                )
            else:
                return SkillResult(
                    success=False,
                    error=stderr.decode() or f"Exit code: {proc.returncode}",
                    output=stdout.decode()
                )
        except asyncio.TimeoutError:
            proc.kill()
            raise

    async def _execute_bun(
        self,
        skill: Skill,
        args: Dict[str, Any],
        timeout: float
    ) -> SkillResult:
        """Execute a skill via Bun runtime (TypeScript + shell hybrid)."""
        from able.tools.shell.bun_shell import BunShell

        if not BunShell.available():
            return SkillResult(
                success=False,
                error="Bun runtime not installed. Install from https://bun.sh"
            )

        # Read the skill's TypeScript implementation
        impl_path = skill.implementation_path
        if impl_path and impl_path.exists():
            script = impl_path.read_text()
        else:
            return SkillResult(
                success=False,
                error=f"Bun skill implementation not found: {impl_path}"
            )

        # Inject args as environment variables
        env = {f"SKILL_ARG_{k.upper()}": str(v) for k, v in args.items()}

        # Detect mode from file extension or skill metadata
        mode = "hybrid"
        if impl_path.suffix == ".sh":
            mode = "shell"
        elif impl_path.suffix in (".ts", ".tsx", ".js"):
            mode = "ts"

        result = await BunShell.run(script, mode=mode, timeout=timeout, env=env)

        if result.exit_code == 0:
            return SkillResult(
                success=True,
                output=result.stdout,
                logs=[result.stderr] if result.stderr else []
            )
        else:
            return SkillResult(
                success=False,
                error=result.stderr or f"Bun exit code: {result.exit_code}",
                output=result.stdout
            )

    async def validate_args(
        self,
        skill_name: str,
        args: Dict[str, Any]
    ) -> List[str]:
        """Validate arguments against skill's input schema"""
        errors = []
        skill = self.registry.get(skill_name)

        if not skill:
            return [f"Skill not found: {skill_name}"]

        # Check required inputs
        for name, spec in skill.metadata.inputs.items():
            if spec.get('required', False) and name not in args:
                errors.append(f"Missing required argument: {name}")

            if name in args:
                expected_type = spec.get('type', 'string')
                value = args[name]

                # Basic type validation
                type_map = {
                    'string': str,
                    'integer': int,
                    'number': (int, float),
                    'boolean': bool,
                    'array': list,
                    'object': dict,
                }

                if expected_type in type_map:
                    expected = type_map[expected_type]
                    if not isinstance(value, expected):
                        errors.append(
                            f"Argument '{name}' should be {expected_type}, "
                            f"got {type(value).__name__}"
                        )

        # Check for unknown arguments
        known_args = set(skill.metadata.inputs.keys())
        for name in args:
            if name not in known_args:
                errors.append(f"Unknown argument: {name}")

        return errors

    def list_available(self, user_trust_tier: str = "L2_SUGGEST") -> List[Dict]:
        """List skills available to a user at their trust level"""
        trust_levels = ["L1_OBSERVE", "L2_SUGGEST", "L3_BOUNDED", "L4_AUTONOMOUS"]
        user_level = trust_levels.index(user_trust_tier) if user_trust_tier in trust_levels else 0

        available = []
        for skill in self.registry.skills.values():
            required_level = trust_levels.index(skill.metadata.trust_level_required) if skill.metadata.trust_level_required in trust_levels else 1

            if user_level >= required_level:
                available.append({
                    "name": skill.metadata.name,
                    "description": skill.metadata.description,
                    "triggers": skill.metadata.trigger_phrases,
                    "inputs": skill.metadata.inputs,
                    "requires_approval": skill.metadata.requires_approval,
                })

        return available
