"""Tool decorator and registry for ATLAS agents."""

import inspect
from dataclasses import dataclass
from typing import Callable, get_type_hints


@dataclass
class ToolDefinition:
    """A tool that an agent can invoke."""

    name: str
    description: str
    handler: Callable
    parameters: dict  # JSON Schema
    is_read_only: bool = False
    is_destructive: bool = False

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def Tool(
    name: str = None,
    description: str = "",
    is_read_only: bool = False,
    is_destructive: bool = False,
):
    """Decorator to register a function as an ATLAS tool.

    Usage:
        @Tool(name="search", description="Search documents")
        async def search(query: str, limit: int = 10) -> str:
            ...
    """

    def decorator(func):
        tool_name = name or func.__name__

        # Auto-generate JSON schema from type hints
        hints = get_type_hints(func)
        sig = inspect.signature(func)
        properties = {}
        required = []

        type_map = {str: "string", int: "integer", float: "number", bool: "boolean"}

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "return"):
                continue
            hint = hints.get(param_name, str)
            prop = {"type": type_map.get(hint, "string")}
            if param.default is inspect.Parameter.empty:
                required.append(param_name)
            else:
                prop["default"] = param.default
            properties[param_name] = prop

        schema = {"type": "object", "properties": properties, "required": required}

        tool_def = ToolDefinition(
            name=tool_name,
            description=description or func.__doc__ or "",
            handler=func,
            parameters=schema,
            is_read_only=is_read_only,
            is_destructive=is_destructive,
        )
        func._tool_definition = tool_def
        return func

    return decorator
