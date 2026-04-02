"""
Slash Commands System

Register and execute slash commands with auto-discovery and help generation.
"""

import asyncio
import inspect
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import logging

logger = logging.getLogger(__name__)


class CommandCategory(str, Enum):
    """Command categories for organization"""
    SYSTEM = "system"
    MEMORY = "memory"
    TOOLS = "tools"
    BILLING = "billing"
    SKILLS = "skills"
    RESEARCH = "research"
    WRITE = "write"
    DEBUG = "debug"


@dataclass
class CommandArgument:
    """Definition of a command argument"""
    name: str
    description: str
    required: bool = True
    default: Any = None
    arg_type: type = str
    choices: Optional[List[str]] = None


@dataclass
class SlashCommand:
    """A registered slash command"""
    name: str
    description: str
    handler: Callable
    category: CommandCategory = CommandCategory.SYSTEM
    aliases: List[str] = field(default_factory=list)
    arguments: List[CommandArgument] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)
    requires_approval: bool = False
    hidden: bool = False


class SlashCommandRegistry:
    """
    Registry for slash commands with auto-discovery.

    Features:
    - Command registration with decorators
    - Argument parsing and validation
    - Help generation
    - Category organization
    - Alias support
    """

    def __init__(self):
        self.commands: Dict[str, SlashCommand] = {}
        self.aliases: Dict[str, str] = {}

        # Register built-in commands
        self._register_builtins()

    def register(
        self,
        name: str,
        description: str,
        category: CommandCategory = CommandCategory.SYSTEM,
        aliases: Optional[List[str]] = None,
        arguments: Optional[List[CommandArgument]] = None,
        examples: Optional[List[str]] = None,
        requires_approval: bool = False,
        hidden: bool = False,
    ) -> Callable:
        """Decorator to register a slash command"""
        def decorator(handler: Callable) -> Callable:
            command = SlashCommand(
                name=name,
                description=description,
                handler=handler,
                category=category,
                aliases=aliases or [],
                arguments=arguments or [],
                examples=examples or [],
                requires_approval=requires_approval,
                hidden=hidden,
            )

            self.commands[name] = command

            # Register aliases
            for alias in command.aliases:
                self.aliases[alias] = name

            logger.debug(f"Registered command: /{name}")
            return handler

        return decorator

    def get_command(self, name: str) -> Optional[SlashCommand]:
        """Get a command by name or alias"""
        # Check direct match
        if name in self.commands:
            return self.commands[name]

        # Check aliases
        if name in self.aliases:
            return self.commands[self.aliases[name]]

        return None

    async def execute(
        self,
        input_text: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Any]:
        """
        Parse and execute a slash command.

        Returns (success, result)
        """
        context = context or {}

        # Parse command
        match = re.match(r'^/(\w+)(?:\s+(.*))?$', input_text.strip())
        if not match:
            return False, "Invalid command format. Use /command [args]"

        command_name = match.group(1).lower()
        args_str = match.group(2) or ""

        # Get command
        command = self.get_command(command_name)
        if not command:
            similar = self._find_similar(command_name)
            suggestion = f" Did you mean /{similar}?" if similar else ""
            return False, f"Unknown command: /{command_name}.{suggestion}"

        # Parse arguments
        try:
            args, kwargs = self._parse_arguments(args_str, command.arguments)
        except ValueError as e:
            return False, f"Argument error: {e}"

        # Execute handler
        try:
            handler = command.handler

            # Check if async
            if asyncio.iscoroutinefunction(handler):
                result = await handler(*args, context=context, **kwargs)
            else:
                result = handler(*args, context=context, **kwargs)

            return True, result

        except Exception as e:
            logger.error(f"Command /{command_name} failed: {e}")
            return False, f"Command failed: {e}"

    def _parse_arguments(
        self,
        args_str: str,
        arg_defs: List[CommandArgument],
    ) -> Tuple[List[Any], Dict[str, Any]]:
        """Parse arguments from string"""
        args = []
        kwargs = {}

        # Simple tokenization (handles quotes)
        tokens = self._tokenize(args_str)

        # Map tokens to arguments
        for i, arg_def in enumerate(arg_defs):
            if i < len(tokens):
                value = self._convert_type(tokens[i], arg_def.arg_type)

                # Validate choices
                if arg_def.choices and value not in arg_def.choices:
                    raise ValueError(
                        f"{arg_def.name} must be one of: {', '.join(arg_def.choices)}"
                    )

                args.append(value)
            elif arg_def.required:
                raise ValueError(f"Missing required argument: {arg_def.name}")
            else:
                args.append(arg_def.default)

        return args, kwargs

    def _tokenize(self, text: str) -> List[str]:
        """Tokenize argument string, handling quotes"""
        tokens = []
        current = ""
        in_quotes = False
        quote_char = None

        for char in text:
            if char in '"\'':
                if in_quotes and char == quote_char:
                    in_quotes = False
                    quote_char = None
                elif not in_quotes:
                    in_quotes = True
                    quote_char = char
                else:
                    current += char
            elif char.isspace() and not in_quotes:
                if current:
                    tokens.append(current)
                    current = ""
            else:
                current += char

        if current:
            tokens.append(current)

        return tokens

    def _convert_type(self, value: str, target_type: type) -> Any:
        """Convert string value to target type"""
        if target_type == bool:
            return value.lower() in ("true", "yes", "1", "on")
        elif target_type == int:
            return int(value)
        elif target_type == float:
            return float(value)
        return value

    def _find_similar(self, name: str) -> Optional[str]:
        """Find similar command name for suggestions"""
        candidates = list(self.commands.keys()) + list(self.aliases.keys())

        # Simple prefix match
        for candidate in candidates:
            if candidate.startswith(name[:2]):
                return candidate

        return None

    def generate_help(self, command_name: Optional[str] = None) -> str:
        """Generate help text"""
        if command_name:
            command = self.get_command(command_name)
            if not command:
                return f"Unknown command: /{command_name}"

            return self._format_command_help(command)

        # Generate full help
        lines = ["**Available Commands**\n"]

        # Group by category
        by_category: Dict[CommandCategory, List[SlashCommand]] = {}
        for cmd in self.commands.values():
            if cmd.hidden:
                continue
            if cmd.category not in by_category:
                by_category[cmd.category] = []
            by_category[cmd.category].append(cmd)

        for category in CommandCategory:
            if category not in by_category:
                continue

            lines.append(f"\n**{category.value.upper()}**")
            for cmd in sorted(by_category[category], key=lambda c: c.name):
                aliases = f" (aliases: {', '.join(cmd.aliases)})" if cmd.aliases else ""
                lines.append(f"  /{cmd.name}{aliases} - {cmd.description}")

        lines.append("\nUse `/help <command>` for detailed help.")
        return "\n".join(lines)

    def _format_command_help(self, command: SlashCommand) -> str:
        """Format detailed help for a command"""
        lines = [
            f"**/{command.name}**",
            f"{command.description}",
            "",
        ]

        if command.aliases:
            lines.append(f"**Aliases:** {', '.join(command.aliases)}")

        if command.arguments:
            lines.append("\n**Arguments:**")
            for arg in command.arguments:
                req = "required" if arg.required else f"optional, default: {arg.default}"
                lines.append(f"  - `{arg.name}` ({req}): {arg.description}")

        if command.examples:
            lines.append("\n**Examples:**")
            for example in command.examples:
                lines.append(f"  `{example}`")

        if command.requires_approval:
            lines.append("\n⚠️ This command requires approval.")

        return "\n".join(lines)

    def _register_builtins(self) -> None:
        """Register built-in commands"""

        @self.register(
            name="help",
            description="Show available commands or help for a specific command",
            category=CommandCategory.SYSTEM,
            aliases=["h", "?"],
            arguments=[
                CommandArgument(
                    name="command",
                    description="Command to get help for",
                    required=False,
                )
            ],
            examples=["/help", "/help status"],
        )
        async def help_command(command: Optional[str] = None, context: dict = None):
            return self.generate_help(command)

        @self.register(
            name="status",
            description="Show current system status",
            category=CommandCategory.SYSTEM,
            aliases=["s"],
        )
        async def status_command(context: dict = None):
            # This will be overridden by the main system
            return "Status: OK"

        @self.register(
            name="clear",
            description="Clear conversation context",
            category=CommandCategory.SYSTEM,
        )
        async def clear_command(context: dict = None):
            return "Context cleared."

        @self.register(
            name="clock",
            description="Clock in/out for billing",
            category=CommandCategory.BILLING,
            arguments=[
                CommandArgument(
                    name="action",
                    description="in or out",
                    choices=["in", "out"],
                ),
                CommandArgument(
                    name="client",
                    description="Client ID",
                    required=False,
                ),
            ],
            examples=["/clock in acme", "/clock out"],
        )
        async def clock_command(action: str, client: Optional[str] = None, context: dict = None):
            if action == "in" and not client:
                return "Error: Client required for clock in"
            return f"Clocked {action}" + (f" for {client}" if client else "")

        @self.register(
            name="mesh",
            description="Execute goal with agent swarm",
            category=CommandCategory.TOOLS,
            arguments=[
                CommandArgument(
                    name="goal",
                    description="Goal to accomplish",
                ),
            ],
            examples=["/mesh research competitor pricing strategies"],
        )
        async def mesh_command(goal: str, context: dict = None):
            return f"Initiating mesh workflow for: {goal}"

        @self.register(
            name="remember",
            description="Store something in memory",
            category=CommandCategory.MEMORY,
            arguments=[
                CommandArgument(
                    name="content",
                    description="What to remember",
                ),
            ],
            examples=["/remember Prefers concise communication"],
        )
        async def remember_command(content: str, context: dict = None):
            return f"Remembered: {content}"

        @self.register(
            name="recall",
            description="Search memory",
            category=CommandCategory.MEMORY,
            arguments=[
                CommandArgument(
                    name="query",
                    description="What to search for",
                ),
            ],
            examples=["/recall communication preferences"],
        )
        async def recall_command(query: str, context: dict = None):
            return f"Searching memory for: {query}"

        @self.register(
            name="research",
            description="Research a topic on the web",
            category=CommandCategory.RESEARCH,
            arguments=[
                CommandArgument(
                    name="topic",
                    description="Topic to research",
                ),
            ],
            examples=["/research latest AI developments 2026"],
        )
        async def research_command(topic: str, context: dict = None):
            return f"Researching: {topic}"

        @self.register(
            name="write",
            description="Generate content with copywriting skills",
            category=CommandCategory.WRITE,
            arguments=[
                CommandArgument(
                    name="type",
                    description="Type of content",
                    choices=["email", "post", "ad", "landing", "response"],
                ),
                CommandArgument(
                    name="brief",
                    description="What to write about",
                ),
            ],
            examples=["/write email Follow up with prospect about demo"],
        )
        async def write_command(content_type: str, brief: str, context: dict = None):
            return f"Writing {content_type}: {brief}"

        @self.register(
            name="skill",
            description="Run a specific skill",
            category=CommandCategory.SKILLS,
            arguments=[
                CommandArgument(
                    name="skill_name",
                    description="Name of the skill to run",
                ),
            ],
            examples=["/skill copywriting"],
        )
        async def skill_command(skill_name: str, context: dict = None):
            return f"Running skill: {skill_name}"


# Global registry instance
_registry: Optional[SlashCommandRegistry] = None


def get_command_registry() -> SlashCommandRegistry:
    """Get global command registry"""
    global _registry
    if _registry is None:
        _registry = SlashCommandRegistry()
    return _registry


def command(
    name: str,
    description: str,
    **kwargs,
) -> Callable:
    """Convenience decorator for registering commands"""
    return get_command_registry().register(name, description, **kwargs)
