"""
Skill source adapter -- wraps SkillRegistry + SkillExecutor as a ToolSourceManager.

Namespace convention: skill:{skill_name}
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..registry import ToolCategory, ToolDefinition, ToolResult, ToolSourceManager

logger = logging.getLogger(__name__)

# Lazy imports
_SkillRegistry = None
_SkillExecutor = None

# Trust level string -> integer mapping (matches executor.py ordering)
_TRUST_INT: Dict[str, int] = {
    "L1_OBSERVE": 1,
    "L2_SUGGEST": 2,
    "L3_BOUNDED": 3,
    "L4_AUTONOMOUS": 4,
}


def _ensure_imports():
    global _SkillRegistry, _SkillExecutor
    if _SkillRegistry is not None:
        return
    try:
        from able.skills.registry import SkillRegistry
        from able.skills.executor import SkillExecutor
        _SkillRegistry = SkillRegistry
        _SkillExecutor = SkillExecutor
    except ImportError:
        logger.debug("SkillRegistry / SkillExecutor not importable -- skill source unavailable")


def _input_schema_from_metadata(inputs: Dict[str, Dict]) -> Dict[str, Any]:
    """Convert SkillMetadata.inputs to a JSON Schema object."""
    if not inputs:
        return {"type": "object", "properties": {}}

    properties: Dict[str, Any] = {}
    required: List[str] = []

    for param_name, spec in inputs.items():
        prop: Dict[str, Any] = {
            "type": spec.get("type", "string"),
        }
        if "description" in spec:
            prop["description"] = spec["description"]
        properties[param_name] = prop
        if spec.get("required", False):
            required.append(param_name)

    schema: Dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


class SkillSource:
    """
    ToolSourceManager adapter for the ABLE skill system.

    Wraps ``SkillRegistry`` for discovery and ``SkillExecutor`` for
    execution.  Each registered skill becomes ``skill:{skill_name}``.
    """

    def __init__(
        self,
        registry: Optional[Any] = None,
        executor: Optional[Any] = None,
    ) -> None:
        """
        Args:
            registry: An existing ``SkillRegistry`` instance.
            executor: An existing ``SkillExecutor`` instance.  If ``None``
                      a default one wrapping the registry will be created.
        """
        _ensure_imports()
        self._registry = registry
        self._executor = executor
        self._tools_cache: List[ToolDefinition] = []

    # -- Protocol properties -----------------------------------------------

    @property
    def name(self) -> str:
        return "skill"

    @property
    def category(self) -> ToolCategory:
        return ToolCategory.SKILL

    # -- Lifecycle ----------------------------------------------------------

    def set_registry(self, registry: Any) -> None:
        self._registry = registry

    def set_executor(self, executor: Any) -> None:
        self._executor = executor

    def _get_executor(self) -> Optional[Any]:
        """Lazy-create an executor if none was injected."""
        if self._executor is not None:
            return self._executor
        if self._registry is None or _SkillExecutor is None:
            return None
        try:
            self._executor = _SkillExecutor(registry=self._registry)
            return self._executor
        except Exception:
            logger.exception("Failed to create default SkillExecutor")
            return None

    def is_available(self) -> bool:
        _ensure_imports()
        return self._registry is not None and _SkillRegistry is not None

    # -- Tool discovery -----------------------------------------------------

    async def list_tools(self) -> List[ToolDefinition]:
        if self._tools_cache:
            return list(self._tools_cache)
        await self.refresh()
        return list(self._tools_cache)

    async def refresh(self) -> int:
        """Reload the skill index and rebuild the tool catalog."""
        if self._registry is None:
            self._tools_cache = []
            return 0

        # Reload from disk if the registry supports it
        try:
            self._registry.load_index()
        except Exception:
            logger.debug("load_index() failed -- using already-loaded skills")

        definitions: List[ToolDefinition] = []
        for meta in self._registry.list_all():
            qualified = f"skill:{meta.name}"
            trust_int = _TRUST_INT.get(meta.trust_level_required, 2)

            tags = ["skill"]
            if meta.trigger_phrases:
                tags.append("trigger")
            if meta.cron_schedule:
                tags.append("scheduled")

            definitions.append(
                ToolDefinition(
                    name=qualified,
                    display_name=meta.name,
                    description=meta.description,
                    category=ToolCategory.SKILL,
                    source=self.name,
                    input_schema=_input_schema_from_metadata(meta.inputs),
                    requires_approval=meta.requires_approval,
                    trust_level=trust_int,
                    tags=tags,
                    metadata={
                        "version": meta.version,
                        "author": meta.author,
                        "use_count": meta.use_count,
                        "trigger_phrases": meta.trigger_phrases,
                        "dependencies": meta.dependencies,
                        "required_tools": meta.required_tools,
                    },
                )
            )

        self._tools_cache = definitions
        logger.info("Skill source refreshed: %d skills", len(definitions))
        return len(definitions)

    # -- Execution ----------------------------------------------------------

    async def call_tool(self, tool_name: str, args: Dict[str, Any]) -> ToolResult:
        """
        Execute a skill.

        ``tool_name`` is the skill name without the ``skill:`` prefix.
        Delegates to ``SkillExecutor.execute()``.
        """
        executor = self._get_executor()
        if executor is None:
            return ToolResult(
                success=False,
                output=None,
                error="SkillExecutor not available",
            )

        try:
            skill_result = await executor.execute(
                skill_name=tool_name,
                args=args,
            )
            return ToolResult(
                success=skill_result.success,
                output=skill_result.output,
                error=skill_result.error,
                execution_time_ms=skill_result.execution_time_ms,
            )
        except Exception as exc:
            logger.exception("Skill execution failed: %s", tool_name)
            return ToolResult(
                success=False,
                output=None,
                error=str(exc),
            )
